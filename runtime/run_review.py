#!/usr/bin/env python3
"""The second opinion, the messages that carry it, and the gate a department comes in by."""
from __future__ import annotations

import argparse
import json

from runtime.run_state import (CLASSIFICATIONS, DEFAULT_ORG, ID_RE, MESSAGE_KINDS, RUNS,
                               agent_exists, mutate, now, policy, read_events)
from runtime.run_outcomes import (charge, evidence_path, release_dependents, verify_submission)
from runtime.run_verbs import (create_run, request_step)


def gate(args) -> None:
    """Put one department's outward action through the runtime, and let the gate decide.

    This is how a department that works in a conversation -- not in a planned run -- gets
    the same governance as one that does. It builds a one-step run for the action, then
    requests that step, so the *policy* classifies it and the runtime does the rest: a
    yellow action parks at `awaiting_approval` and appears in the console's queue, a red one
    blocks for a human, and either way the audit entry is written by the gate.

    That last part is the whole point. `CLAUDE.md` §3 says the record is a side effect of
    the gate and never something an agent chooses to write, and `runtime/audit.py` has no
    `append` command precisely so an agent cannot be handed one. Before this there was no
    other route, so sixteen of seventeen departments logged nothing at all -- the skills
    asked agents to write entries by hand, which is the thing the constitution forbids.

    Prints the run id. Nothing here sends, publishes or spends: it records the intent and
    stops. The action itself still waits for a person.
    """
    if args.action not in policy():
        raise SystemExit(f"action is not policy-classified: {args.action}")
    if not agent_exists(args.owner):
        raise SystemExit(f"unknown department: {args.owner}")
    summary = str(args.summary).strip()
    if not 10 <= len(summary) <= 500:
        raise SystemExit("summary must be 10..500 characters -- it is what the approver reads")
    run_id = args.run_id
    workflow = {
        "version": 1, "id": f"wf-{run_id}", "goal": summary, "max_cycles": 4,
        "steps": [{"id": "act", "owner": args.owner, "action": args.action,
                   "depends_on": [], "max_attempts": 1}],
    }
    RUNS.mkdir(parents=True, exist_ok=True)
    destination = RUNS / f"{run_id}.gate.json"
    destination.write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
    import io
    from contextlib import redirect_stdout
    with redirect_stdout(io.StringIO()):
        create_run(argparse.Namespace(workflow=str(destination), run_id=run_id,
                                      actor=args.owner,
                                      org=getattr(args, "org", None) or DEFAULT_ORG,
                                      request_id=f"gate-create-{args.request_id}", spend=0.0))
    # Both of those print their own line; this command has one answer, which is what the
    # gate decided.
    with redirect_stdout(io.StringIO()):
        request_step(argparse.Namespace(run_id=run_id, step="act", actor=args.owner,
                                        holder=args.owner,
                                        request_id=f"gate-request-{args.request_id}"))
    print(read_events(run_id)[-1]["steps"]["act"]["status"])


def send_message(args) -> None:
    if not ID_RE.fullmatch(args.message_id): raise SystemExit("invalid message id")
    proof,proof_hash=evidence_path(args.payload)
    if args.kind not in MESSAGE_KINDS: raise SystemExit("invalid message kind")
    if args.classification not in CLASSIFICATIONS: raise SystemExit("restricted or invalid classification")
    if not 1 <= len(args.subject.strip()) <= 160: raise SystemExit("subject must be 1..160 characters")
    def change(state):
        if any(item["id"] == args.message_id for item in state["messages"]): raise SystemExit("message id already exists")
        step=state["steps"].get(args.step)
        if not step: raise SystemExit(f"unknown step: {args.step}")
        current={step["owner"]}
        if step.get("checker"): current.add(step["checker"])
        adjacent=set()
        for dependency in step["depends_on"]:
            adjacent.add(state["steps"][dependency]["owner"])
            if state["steps"][dependency].get("checker"): adjacent.add(state["steps"][dependency]["checker"])
        for candidate in state["steps"].values():
            if args.step in candidate["depends_on"]:
                adjacent.add(candidate["owner"])
                if candidate.get("checker"): adjacent.add(candidate["checker"])
        pair={args.from_agent,args.to_agent}
        if not (pair <= current or (pair & current and pair & adjacent)): raise SystemExit("message participants must share this step or an adjacent workflow edge")
        if args.from_agent == args.to_agent: raise SystemExit("sender and receiver must differ")
        if args.reply_to:
            prior=next((item for item in state["messages"] if item["id"] == args.reply_to),None)
            if not prior: raise SystemExit("reply_to message does not exist")
            if prior["step"] != args.step or prior["from"] != args.to_agent or prior["to"] != args.from_agent: raise SystemExit("reply must reverse the original direction within the same step")
        state["messages"].append({"id":args.message_id,"step":args.step,"from":args.from_agent,"to":args.to_agent,"kind":args.kind,"subject":args.subject.strip(),"classification":args.classification,"payload":proof,"payload_sha256":proof_hash,"reply_to":args.reply_to,"sent_at":now()})
    mutate(args.run_id,args.request_id,"message.sent",args.from_agent,args.message_id,change); print(args.message_id)


def checker_message(state: dict, step_id: str, message_id: str, checker: str, kind: str = "feedback") -> dict:
    step=state["steps"][step_id]
    message=next((item for item in state["messages"] if item["id"] == message_id),None)
    if not message: raise SystemExit("checker message does not exist")
    if message["step"] != step_id: raise SystemExit("checker message belongs to another step")
    if message["from"] != checker or message["to"] != step["owner"] or message["kind"] != kind: raise SystemExit(f"checker must send {kind} to the maker")
    _,current_hash=evidence_path(message["payload"])
    if current_hash != message["payload_sha256"]: raise SystemExit("checker message artifact changed after sending")
    return message


def check_approve(args) -> None:
    def change(state):
        step=state["steps"].get(args.step)
        if not step or step["status"] != "awaiting_check": raise SystemExit(f"step is not awaiting check: {args.step}")
        if args.actor != step.get("checker"): raise SystemExit(f"step checker is {step.get('checker')}, not {args.actor}")
        verify_submission(step)
        step.update(status="completed",checked_by=args.actor,check_message=args.message_id)
        checker_message(state,args.step,args.message_id,args.actor,"decision")
        charge(state, step, args)  # B-04: the review call is half the bill on a RETURN loop
        release_dependents(state)
    state=mutate(args.run_id,args.request_id,"check.approved",args.actor,args.step,change); print(state["run_status"])


def check_return(args) -> None:
    def change(state):
        step=state["steps"].get(args.step)
        if not step or step["status"] != "awaiting_check": raise SystemExit(f"step is not awaiting check: {args.step}")
        if args.actor != step.get("checker"): raise SystemExit(f"step checker is {step.get('checker')}, not {args.actor}")
        verify_submission(step); checker_message(state,args.step,args.message_id,args.actor); charge(state, step, args)
        if step["review_cycles"] >= step["max_review_cycles"]:
            step["status"]="blocked_review_limit"; state["run_status"]="blocked_review_limit"
        else:
            step["review_cycles"]+=1; step["status"]="ready"; step["last_feedback_message"]=args.message_id
    state=mutate(args.run_id,args.request_id,"check.returned",args.actor,args.step,change); print(state["steps"][args.step]["status"])


def check_reject(args) -> None:
    def change(state):
        step=state["steps"].get(args.step)
        if not step or step["status"] != "awaiting_check": raise SystemExit(f"step is not awaiting check: {args.step}")
        if args.actor != step.get("checker"): raise SystemExit(f"step checker is {step.get('checker')}, not {args.actor}")
        verify_submission(step); checker_message(state,args.step,args.message_id,args.actor); charge(state, step, args)
        step.update(status="rejected_by_checker",last_feedback_message=args.message_id); state["run_status"]="rejected_by_checker"
    mutate(args.run_id,args.request_id,"check.rejected",args.actor,args.step,change); print("rejected_by_checker")
