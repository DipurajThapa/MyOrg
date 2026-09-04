#!/usr/bin/env python3
"""How a step ends: what it cost, what it produced, and what that frees.

Also the two ways a run stops early -- a budget extended, or a person cancelling it."""
from __future__ import annotations

import hashlib
import json

from runtime import audit as audit_log
from runtime.run_state import (ROOT, append_event, attribution, audit_evidence, check_claim,
                               mutate, now, read_events, release_claim, run_lock)


def evidence_path(value: str) -> tuple[str,str]:
    path=(ROOT/value).resolve()
    try: relative=path.relative_to(ROOT)
    except ValueError as error: raise SystemExit("evidence must be inside the repository") from error
    if not path.is_file(): raise SystemExit(f"evidence file does not exist: {relative}")
    return str(relative),hashlib.sha256(path.read_bytes()).hexdigest()


def release_dependents(state: dict) -> None:
    for candidate in state["steps"].values():
        if candidate["status"] == "pending" and all(state["steps"][dependency]["status"] == "completed" for dependency in candidate["depends_on"]): candidate["status"]="ready"
    if all(item["status"] == "completed" for item in state["steps"].values()): state["run_status"]="completed"


def verify_submission(step: dict) -> None:
    if not step.get("submissions"): raise SystemExit("step has no submission")
    submission=step["submissions"][-1]
    _,current_hash=evidence_path(submission["evidence"])
    if current_hash != submission["evidence_sha256"]: raise SystemExit("submission artifact changed after maker handoff")


def charge(state: dict, step: dict, args) -> None:
    """Add what this dispatch cost to the step and to the run.

    Recorded on the transition the dispatch was going to make anyway -- completed, failed or
    held -- rather than on a mutation of its own. Every mutation spends a cycle, and the
    planner budgets roughly two per step, so a dedicated `spend` event would have made every
    step cost an extra cycle to say what it had already cost in money.

    Consequence worth knowing: a dispatch that dies before any of those three transitions is
    not counted. That undercounts, never over -- and undercounting is the safe direction for
    a figure a ceiling is read from, because the ceiling then trips late rather than early.

    What rides here (B-04, measured 2026-09-03): the maker's call and its grade on
    complete/fail/hold; the checker's review on check-approve/return/reject; the planner's
    attempts as a seed at create_run. Not charged: the approval brief (see `briefing`), and
    the last dispatch of a cancelled run (its transition is refused).
    """
    amount = round(float(getattr(args, "spend", 0.0) or 0.0), 6)
    if amount <= 0:
        return
    step["spend_usd"] = round(step.get("spend_usd", 0.0) + amount, 6)
    state["spend_usd"] = round(state.get("spend_usd", 0.0) + amount, 6)


def extend_budget(args) -> None:
    """Give a run that exhausted its cycle budget more, and put it back to work.

    REC-11: `blocked_cycle_limit` was terminal, so a run that ran out mid-flight stranded
    every step it had already finished and re-driving it returned quietly -- an operator
    retrying could not tell the difference between "resumed" and "did nothing".

    Only the cycle budget needs this. The *cost* ceiling parks a step at
    `awaiting_approval` instead of ending the run, so it already resumes through `approve`
    -- which is the whole reason it was built on `hold`. One resume path, not two.

    Writes its own event rather than going through `mutate`, because `mutate` refuses a
    terminal run and this is the one transition whose entire purpose is to leave one.
    """
    if not str(getattr(args, "approver", "")).strip():
        raise SystemExit("extending a budget is a human decision -- who approved it?")
    extra = int(args.cycles)
    if not 1 <= extra <= 100:
        raise SystemExit("extension must be 1..100 cycles")
    with run_lock(args.run_id):
        events = read_events(args.run_id)
        state = json.loads(json.dumps(events[-1]))
        # Replay first, then validity. A second call with the same request id is the *same*
        # extension arriving twice, and by then the run is active again -- so checking the
        # status first would reject a replay for having succeeded.
        if any(e.get("request_id") == args.request_id for e in events):
            print("idempotent replay"); return
        if state["run_status"] != "blocked_cycle_limit":
            raise SystemExit(f"run is {state['run_status']}, not out of cycle budget")
        ceiling = min(100, state["max_cycles"] + extra)
        if ceiling <= state["max_cycles"]:
            raise SystemExit(f"max_cycles is already at the {ceiling} ceiling")
        state.update(seq=state["seq"] + 1, event="run.budget_extended", actor=args.approver,
                     target=args.run_id, request_id=args.request_id, ts=now(),
                     run_status="active", max_cycles=ceiling)
        audit_log.append(actor=args.approver, action="run.budget_extended", category="yellow",
                         target=args.run_id, approval="granted",
                         evidence=audit_evidence(args.run_id), outcome="ok",
                         note=f"cycle budget raised to {ceiling}; {state['cycle_count']} already spent")
        append_event(args.run_id, state)
    print(f"active\tmax_cycles={ceiling}")


def cancel_run(args) -> None:
    """A named human stops a run that is still going. Terminal; nothing is deleted.

    B-02. Every other stop in this runtime is the machine stopping itself -- a ceiling, a
    retry limit, a gate the plan happened to contain. `reject` is the only human stop and it
    only works on a step already parked, so a run of green steps could not be stopped at
    all. This is the missing verb: it ends the run through the same path as every other
    terminal state, keeps every artifact, and records who did it and why.

    A dispatch already in flight finishes on its own and then fails its `complete` against
    the terminal run. That last attempt's cost is charged on the transition that is refused,
    so a cancelled run under-reports by at most one dispatch and one grade. Accepted: the
    alternative is a spend event that costs a cycle to say what was already spent.
    """
    if not str(getattr(args, "approver", "")).strip():
        raise SystemExit("cancelling a run is a human decision -- who did it?")
    reason = str(getattr(args, "reason", "")).strip()
    if not 1 <= len(reason) <= 200:
        raise SystemExit("a cancel needs a reason of 1..200 characters")
    def change(state):
        state.update(run_status="cancelled", cancelled_by=args.approver, cancel_reason=reason)
        for step in state["steps"].values():
            if step["status"] == "in_progress": release_claim(step)  # nobody may finish it now
    def audit(state):
        done = sum(s["status"] == "completed" for s in state["steps"].values())
        return {"actor": args.approver, "action": "run.cancelled", "category": "yellow",
                "target": args.run_id, "approval": "granted", "outcome": "blocked",
                "note": f"{attribution(state, args.approver, getattr(args, 'actor_id', None)).replace('approved by', 'stopped by', 1)} "
                        f"after {done}/{len(state['steps'])} steps: {reason}"}
    mutate(args.run_id, args.request_id, "run.cancelled", args.approver, args.run_id, change, audit)
    print("cancelled")


def complete(args) -> None:
    proof,proof_hash=evidence_path(args.evidence)
    def change(state):
        if args.revision != state["workflow_revision"]: raise SystemExit("stale workflow revision")
        step=state["steps"].get(args.step)
        if not step or step["status"] != "in_progress": raise SystemExit(f"step is not in progress: {args.step}")
        if args.actor != step["owner"]: raise SystemExit(f"step owner is {step['owner']}, not {args.actor}")
        submission={"submission_revision":len(step["submissions"])+1,"evidence":proof,"evidence_sha256":proof_hash,"maker":args.actor,"submitted_at":now()}
        step["submissions"].append(submission)
        check_claim(step, getattr(args, "claim_token", None))
        step.update(status="awaiting_check" if step.get("checker") else "completed", evidence=proof, evidence_sha256=proof_hash)
        release_claim(step)
        charge(state, step, args)
        if not step.get("checker"): release_dependents(state)
    state=mutate(args.run_id,args.request_id,"step.completed",args.actor,args.step,change); print(state["run_status"])


def fail(args) -> None:
    def change(state):
        step=state["steps"].get(args.step)
        if not step or step["status"] != "in_progress": raise SystemExit(f"step is not in progress: {args.step}")
        if args.actor != step["owner"]: raise SystemExit(f"step owner is {step['owner']}, not {args.actor}")
        check_claim(step, getattr(args, "claim_token", None))
        step["last_failure"]=args.reason
        release_claim(step)
        charge(state, step, args)
        step["status"]="ready" if step["attempts"] < step["max_attempts"] else "blocked_retry_limit"
        if step["status"] == "blocked_retry_limit": state["run_status"]="blocked_retry_limit"
    state=mutate(args.run_id,args.request_id,"step.failed",args.actor,args.step,change); print(state["steps"][args.step]["status"])


def release_step(args) -> None:
    """Put back a step whose attempt never happened, and give back the attempt.

    `request_step` spends an attempt *before* the model is called, which is right when the
    call is made and wrong when it never is: an overloaded API returned zero tokens twice and
    a step burned its whole budget without a single word being generated. This returns the
    step to `ready` and un-spends the attempt, because there was nothing to attempt.

    Distinct from `fail`, which records an attempt that was made and did not work, and from
    `expire_claim`, which is an operator forcing a claim open. This is the driver saying "the
    other end was busy" -- so it never blocks a run, and a step it releases keeps its full
    retry budget for a real try later.
    """
    def change(state):
        step = state["steps"].get(args.step)
        if not step or step["status"] != "in_progress": raise SystemExit(f"step is not in progress: {args.step}")
        if args.actor != step["owner"]: raise SystemExit(f"step owner is {step['owner']}, not {args.actor}")
        check_claim(step, getattr(args, "claim_token", None))
        step["last_failure"] = args.reason
        release_claim(step)
        charge(state, step, args)
        # Never below zero, and never above what was spent: releasing twice cannot mint
        # attempts a step was never granted.
        step["attempts"] = max(0, step["attempts"] - 1)
        step["releases"] = step.get("releases", 0) + 1
        step["status"] = "ready"
    state = mutate(args.run_id, args.request_id, "step.released", args.actor, args.step, change)
    print(state["steps"][args.step]["status"])
