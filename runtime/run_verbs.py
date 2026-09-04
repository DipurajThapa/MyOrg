#!/usr/bin/env python3
"""A step's life before it ends: created, requested, claimed, approved, refused, parked."""
from __future__ import annotations

import os
from pathlib import Path

from runtime.run_state import (CLAIM_SECONDS, DEFAULT_ORG, RUNS, append_event, attribution,
                               canonical, check_claim, claim_is_live, load_json, mint_claim,
                               mutate, now, policy, release_claim, revision, run_lock, run_path,
                               stamp, validate_workflow)
from runtime.run_outcomes import (charge, evidence_path)


def create_run(args) -> None:
    workflow_path = Path(args.workflow).resolve()
    workflow = load_json(workflow_path)
    validate_workflow(workflow)
    run_id = args.run_id
    with run_lock(run_id):
        path = run_path(run_id)
        if path.exists(): raise SystemExit(f"run already exists: {run_id}")
        states = {}
        for step in workflow["steps"]:
            states[step["id"]] = {"status":"ready" if not step.get("depends_on") else "pending", "attempts":0, "owner":step["owner"], "checker":step.get("checker"), "review_cycles":0, "max_review_cycles":step.get("max_review_cycles",0), "submissions":[], "action":step["action"], "risk":policy()[step["action"]], "max_attempts":step["max_attempts"], "depends_on":step.get("depends_on", []), "holder":"", "claim_token":"", "claim_expires_at":""}
        state = {"seq":1,"event":"run.created","target":run_id,"request_id":args.request_id,"actor":args.actor,"ts":now(),"run_id":run_id,"org_id":getattr(args,"org",None) or DEFAULT_ORG,"workflow_id":workflow["id"],"workflow_revision":revision(workflow),"goal":workflow["goal"],"max_cycles":workflow["max_cycles"],"cycle_count":0,"run_status":"active","messages":[],"steps":states}
        # B-04: a planned run was paid for before it existed. Seed the figure the ceiling
        # reads so the plan is the first line of the bill, not a call nobody counted.
        planned = round(float(getattr(args, "spend", 0.0) or 0.0), 6)
        if planned > 0: state.update(spend_usd=planned, planning_spend_usd=planned)
        append_event(run_id, state)
        snapshot = RUNS / f"{run_id}.workflow.json"
        snapshot.write_bytes(canonical(workflow) + b"\n")
    print(f"{run_id}\t{state['workflow_revision']}")


def request_step(args) -> None:
    def change(state):
        step = state["steps"].get(args.step)
        if not step or step["status"] != "ready": raise SystemExit(f"step is not ready: {args.step}")
        if args.actor != step["owner"]: raise SystemExit(f"step owner is {step['owner']}, not {args.actor}")
        if step["risk"] == "red": step["status"] = "blocked_human"; state["run_status"]="blocked_human"
        elif step["risk"] == "yellow": step["status"] = "awaiting_approval"
        else:
            # The cap belongs here, where every dispatch passes, not only on `fail`. A step
            # returned by its checker came back through this door and ran at 4 of a maximum
            # 2: the limit its owner set was enforced on one path out of three, so the
            # per-step spend brake was not a brake.
            if step["attempts"] >= step["max_attempts"]:
                step["status"] = "blocked_retry_limit"
                state["run_status"] = "blocked_retry_limit"
                return
            step["status"] = "in_progress"; step["attempts"] += 1
            mint_claim(state, step, getattr(args, "holder", None) or args.actor)
    def audit(state):
        step = state["steps"][args.step]
        if step["risk"] == "green": return None  # ordinary work is not a gated action
        gated = step["risk"] == "yellow"
        return {"actor": args.actor, "action": step["action"], "category": step["risk"],
                "target": f"{args.run_id}/{args.step}",
                "approval": "pending" if gated else "not-required",
                "outcome": "awaiting-approval" if gated else "refused",
                "note": "parked for a human decision" if gated
                        else "handed back to a human; no code path can approve this"}
    state = mutate(args.run_id,args.request_id,"step.requested",args.actor,args.step,change,audit)
    print(state["steps"][args.step]["status"])


def approve(args) -> None:
    def change(state):
        step=state["steps"].get(args.step)
        if not step or step["status"] != "awaiting_approval": raise SystemExit(f"step is not awaiting approval: {args.step}")
        if not args.approval_ref.strip(): raise SystemExit("approval_ref is required")
        # A human decision is not a retry. This used to spend an attempt, so a step that had
        # already reached its cap came back at 3 of 2 -- waiting for a person cost the step
        # part of the budget its owner set, and the cap stopped meaning anything on this
        # path. Approval resumes the work; it does not re-try it.
        if step.get("held_kind") == "budget":
            # Nothing was produced, so there is nothing to accept: approving buys the work.
            # The step goes back to `ready` to be done properly, the placeholder notice is
            # dropped so it can never be submitted as a deliverable, and the run gets one
            # more ceiling's worth -- otherwise the next pass parks it again on the same
            # spend and the operator approves forever.
            # The ceiling itself is the driver's, so read it from the environment the driver
            # reads rather than importing the driver into the state machine.
            try:
                ceiling = float(os.environ.get("MYORG_RUN_CEILING_USD", "5") or 0.0)
            except ValueError:
                ceiling = 5.0
            ceiling = float(state.get("spend_ceiling_usd") or 0.0) or ceiling
            state["spend_ceiling_usd"] = float(state.get("spend_usd", 0.0) or 0.0) + ceiling
            step.update(status="ready", approver=args.approver, approval_ref=args.approval_ref,
                        held_evidence="", held_evidence_sha256="", held_kind="")
            release_claim(step)
            return
        step.update(status="in_progress", approver=args.approver, approval_ref=args.approval_ref)
        release_claim(step)  # the human hands it back; the next driver claims it
    def audit(state):
        step = state["steps"][args.step]
        return {"actor": args.approver, "action": step["action"], "category": step["risk"],
                "target": f"{args.run_id}/{args.step}", "approval": "granted", "outcome": "ok",
                "note": f"{attribution(state, args.approver, getattr(args, 'actor_id', None))} "
                        f"against reference {args.approval_ref}"}
    mutate(args.run_id,args.request_id,"step.approved",args.approver,args.step,change,audit); print("in_progress")


def reject(args) -> None:
    def change(state):
        step=state["steps"].get(args.step)
        if not step or step["status"] != "awaiting_approval": raise SystemExit(f"step is not awaiting approval: {args.step}")
        step.update(status="rejected", approver=args.approver, approval_ref=args.approval_ref)
        state["run_status"]="rejected"
    def audit(state):
        step = state["steps"][args.step]
        return {"actor": args.approver, "action": step["action"], "category": step["risk"],
                "target": f"{args.run_id}/{args.step}", "approval": "denied", "outcome": "blocked",
                "note": f"{attribution(state, args.approver, getattr(args, 'actor_id', None)).replace('approved', 'declined', 1)} "
                        f"against reference {args.approval_ref}"}
    mutate(args.run_id,args.request_id,"step.rejected",args.approver,args.step,change,audit); print("rejected")


def take(args) -> None:
    """Pick up a step nobody is holding. Refuses to steal live work."""
    def change(state):
        step = state["steps"].get(args.step)
        if not step or step["status"] != "in_progress": raise SystemExit(f"step is not in progress: {args.step}")
        if args.actor != step["owner"]: raise SystemExit(f"step owner is {step['owner']}, not {args.actor}")
        if claim_is_live(step) and step["holder"] != args.holder:
            raise SystemExit(f"{step['holder']} already holds {args.step} until {step['claim_expires_at']}")
        mint_claim(state, step, args.holder)
    state = mutate(args.run_id,args.request_id,"step.taken",args.actor,args.step,change)
    print(state["steps"][args.step]["claim_token"])


def renew_claim(args) -> None:
    """The holder says it is still working; the claim lives another CLAIM_SECONDS.

    B-01. This is the heartbeat, and it is a mutation on purpose: the claim on the step is
    the *only* record of who holds it. A second record kept elsewhere (the old lease file)
    could say "alive" while this one said "expired", and the driver believed this one."""
    def change(state):
        step = state["steps"].get(args.step)
        if not step or step["status"] != "in_progress": raise SystemExit(f"step is not in progress: {args.step}")
        if not claim_is_live(step) or step.get("holder") != args.holder:
            raise SystemExit(f"{args.holder} does not hold {args.step}")
        check_claim(step, args.claim_token)
        step["claim_expires_at"] = stamp(CLAIM_SECONDS)
    state = mutate(args.run_id, args.request_id, "claim.renewed", args.holder, args.step, change)
    print(state["steps"][args.step]["claim_expires_at"])


def expire_claim(args) -> None:
    """Reclaim a step from a holder that is never coming back.

    Deliberately an operator action rather than something the driver may do to itself:
    forcing a claim open is how two holders end up on one step, so it is recorded."""
    def change(state):
        step = state["steps"].get(args.step)
        if not step: raise SystemExit(f"unknown step: {args.step}")
        if not step.get("holder"): raise SystemExit(f"nobody holds {args.step}")
        step["claim_expires_at"] = now()
    def audit(state):
        step = state["steps"][args.step]
        return {"actor": getattr(args, "actor", None) or "operator", "action": step["action"],
                "category": step["risk"], "target": f"{args.run_id}/{args.step}",
                "approval": "not-required", "outcome": "ok",
                "note": "an operator reclaimed a step from a holder that stopped responding"}
    mutate(args.run_id,args.request_id,"claim.expired",getattr(args,"actor",None) or "operator",args.step,change,audit)
    print("expired")


def hold(args) -> None:
    """Park a step because a control could not run -- never because the work was judged bad.

    The deliverable is kept and handed to a person, so the decision a human makes is about
    real work they can read, and the agent never has to produce it twice."""
    proof,proof_hash=evidence_path(args.evidence)
    def change(state):
        step=state["steps"].get(args.step)
        if not step or step["status"] != "in_progress": raise SystemExit(f"step is not in progress: {args.step}")
        if args.actor != step["owner"]: raise SystemExit(f"step owner is {step['owner']}, not {args.actor}")
        check_claim(step, getattr(args, "claim_token", None))
        # Why it was parked decides what approving it means. A control that could not run
        # leaves real work to accept; a cost ceiling leaves nothing, because the step was
        # never dispatched. Recording the difference is what stops a budget notice being
        # handed to a checker as though it were the deliverable -- which it was.
        step.update(status="awaiting_approval", held_reason=args.reason, held_evidence=proof,
                    held_evidence_sha256=proof_hash,
                    held_kind=getattr(args, "kind", None) or "ungraded")
        charge(state, step, args)
        release_claim(step)
    def audit(state):
        step=state["steps"][args.step]
        return {"actor": args.actor, "action": step["action"], "category": step["risk"],
                "target": f"{args.run_id}/{args.step}", "approval": "pending",
                "outcome": "awaiting-approval",
                # The record has to say which decision is being asked for. This was
                # hardcoded to the quality wording, so every cost stop went into the
                # accountability log as a broken gate -- and an auditor reading it would
                # find no quality problem to explain it.
                "note": ("the run reached its cost ceiling, so spending more waits for a person"
                         if step.get("held_kind") == "budget"
                         else "a quality gate could not run, so the work waits for a person")}
    mutate(args.run_id,args.request_id,"step.held",args.actor,args.step,change,audit); print("awaiting_approval")
