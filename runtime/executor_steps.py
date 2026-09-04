#!/usr/bin/env python3
"""One step, moved: dispatched, finished, released, failed, parked -- and stopped when the
money runs out."""
from __future__ import annotations

from runtime.executor_core import (ExecutorError, MAX_REASON_CHARS, StepRequest, agent_brief,
                                   core, current_state, namespace, os, quietly, request_id,
                                   token_for, tools, write_evidence)
from runtime.executor_grading import (last_feedback, upstream_handoffs)


def remembered_for(step_id: str, step: dict, state: dict) -> tuple[str, ...]:
    """Approved company memory that bears on this step. Never blocks the work."""
    from runtime.memory import recall
    try:
        found = recall(f"{state['goal']} {step_id} {step['action']} {step['owner']}")
    except SystemExit:
        return ()
    return tuple(entry.as_prompt_line() for entry in found)
def dispatch(run_id, step_id, step, state, backend) -> tuple:
    """Send the step to its department, in its own room, with only what it may touch.

    Returns the agent's reply and whatever files it left behind."""
    owner = step["owner"]
    room = tools.workspace(run_id, step_id)
    grant = tools.grant_for(owner)
    # A department that can search is told so in the same breath it is handed the tool.
    # Attaching the warning to the *grant* rather than to a department's own file means a
    # future grant cannot be made without it -- which is the only reason this is safe.
    brief = agent_brief(owner)
    if tools.reaches_outward(grant):
        brief += tools.NETWORK_WARNING
    output = backend(StepRequest(run_id=run_id, step_id=step_id, agent=owner,
                                 action=step["action"], goal=state["goal"],
                                 brief=brief,
                                 handoffs=upstream_handoffs(state, step),
                                 feedback=last_feedback(state, step),
                                 remembered=remembered_for(step_id, step, state),
                                 workspace=room, grant=grant))
    return output, tools.produced_files(room)


def finish(run_id: str, step_id: str, owner: str, evidence: str, revision: str,
           spend: float = 0.0) -> str:
    return quietly(core.complete, namespace(
        run_id=run_id, step=step_id, actor=owner, evidence=evidence, spend=spend,
        revision=revision, claim_token=token_for(run_id, step_id),
        request_id=request_id(run_id, step_id, "complete")))


def release_step(run_id: str, step_id: str, owner: str, reason: str,
                 spend: float = 0.0, claim_token: str | None = None,
                 request_id_value: str | None = None) -> None:
    """Hand a step back unattempted, because the other end was busy (see `core.release_step`)."""
    try:
        quietly(core.release_step, namespace(
            run_id=run_id, step=step_id, actor=owner, reason=reason[:MAX_REASON_CHARS], spend=spend,
            claim_token=claim_token or token_for(run_id, step_id),
            request_id=request_id_value or request_id(run_id, step_id, "release")))
    except SystemExit as error:
        raise ExecutorError(f"could not release {step_id}: {error}") from error


def record_failure(run_id: str, step_id: str, owner: str, reason: str,
                   spend: float = 0.0, claim_token: str | None = None,
                   request_id_value: str | None = None) -> None:
    """Hand the failure to the state machine so its retry budget decides what happens.

    A rejected attempt still cost money -- the expensive path in the observed end-to-end run
    was three of these -- so the charge rides here too, not only on success.

    The in-process driver holds the current claim, so it may read the token back from the
    run. An outside worker must *bring* its token (B-01): reading it from the run would let a
    worker whose claim had been taken over write with the new holder's token."""
    try:
        quietly(core.fail, namespace(run_id=run_id, step=step_id, actor=owner,
                                     reason=reason[:MAX_REASON_CHARS], spend=spend,
                                     claim_token=claim_token or token_for(run_id, step_id),
                                     request_id=request_id_value or request_id(run_id, step_id, "fail")))
    except SystemExit as error:
        raise ExecutorError(f"could not record failure on {step_id}: {error}") from error


def hold_for_human(run_id: str, step_id: str, owner: str, output: str,
                   reason: str, log, spend: float = 0.0, claim_token: str | None = None,
                   request_id_value: str | None = None) -> None:
    """Keep the work, park the step, tell a person. Never pass what was not graded."""
    artifact = write_evidence(run_id, step_id, output, "ungraded")
    try:
        quietly(core.hold, namespace(run_id=run_id, step=step_id, actor=owner,
                                     evidence=artifact, reason=reason[:MAX_REASON_CHARS], spend=spend,
                                     claim_token=claim_token or token_for(run_id, step_id),
                                     request_id=request_id_value or request_id(run_id, step_id, "hold")))
    except SystemExit as error:
        raise ExecutorError(f"could not hold {step_id}: {error}") from error
    log(f"  {step_id}: quality gate could not run -- {reason}; held for a human ({artifact})")


def finish_approved_hold(run_id: str, step_id: str, step: dict, state: dict, log) -> bool:
    """A human approved work whose gate could not run. Use it -- do not produce it again.

    Only for that case. A step parked on the cost ceiling was never dispatched, so its
    "evidence" is the budget notice the runtime wrote -- completing with it handed a
    checker a receipt to certify as research, and the checker rightly refused twice and
    ended the run. `approve` sends a budget hold back to `ready` instead, so there is
    nothing here to finish.
    """
    if step.get("held_kind") == "budget":
        return False
    held = step.get("held_evidence")
    if not held:
        return False
    try:
        run_status = finish(run_id, step_id, step["owner"], held, state["workflow_revision"])
    except SystemExit as error:
        raise ExecutorError(f"could not complete {step_id}: {error}") from error
    log(f"  {step_id}: completed with human-approved ungraded work -> {held} (run={run_status})")
    return True


def run_ceiling_usd() -> float:
    """What one run may spend before it stops and asks. 0 disables the ceiling.

    The default comes from measurement, not taste: a real graded step cost about $0.80 warm
    and a cold first dispatch about $2.80, so $5 buys a normal run of five or six steps and
    stops the retry loop that the observed end-to-end run fell into on its third attempt.
    """
    try:
        return max(0.0, float(os.environ.get("MYORG_RUN_CEILING_USD", "5")))
    except ValueError:
        return 5.0


def over_budget(run_id: str, step_id: str, owner: str, state: dict, log) -> bool:
    """Stop before spending, not after. Returns True if the step was parked.

    Deliberately *fails open*. If the spend figure cannot be read, the step runs: a broken
    counter must not be able to halt the company. That is the opposite of the grading rule,
    where an unreadable control parks the work -- and the asymmetry is intentional. A grader
    that cannot run risks shipping bad work; a spend counter that cannot run risks only
    overspending, which the ceiling's own alert catches. Do not "fix" this into failing
    closed without changing that reasoning first.
    """
    # A human who approved a budget stop bought this run more room; that allowance lives
    # on the run, so read it before falling back to the environment's default.
    ceiling = float(state.get("spend_ceiling_usd") or 0.0) or run_ceiling_usd()
    if not ceiling:
        return False
    try:
        # Re-read rather than trust the caller's copy. `advance` loads the run once per
        # iteration and drives every ready step from that snapshot, so a step later in the
        # same pass would otherwise see the spend as it was *before* its siblings ran --
        # and a ceiling that reads a stale figure is not a ceiling.
        spent = float(current_state(run_id).get("spend_usd", 0.0) or 0.0)
    except (TypeError, ValueError, SystemExit, KeyError):
        spent = float(state.get("spend_usd", 0.0) or 0.0) if isinstance(
            state.get("spend_usd", 0.0), (int, float)) else 0.0
    if spent < ceiling:
        return False
    reason = (f"run has spent ${spent:.2f} of its ${ceiling:.2f} ceiling; "
              f"approve to continue or raise MYORG_RUN_CEILING_USD")
    note = (f"This step was not dispatched. The run reached its cost ceiling first.\n\n"
            f"Spent so far: ${spent:.2f}\nCeiling: ${ceiling:.2f}\n\n"
            f"Approving this step continues the run. Nothing has been produced for it yet, "
            f"so approval buys the work rather than accepting it.")
    artifact = write_evidence(run_id, step_id, note, "over-budget")
    try:
        quietly(core.hold, namespace(run_id=run_id, step=step_id, actor=owner,
                                     evidence=artifact, reason=reason[:MAX_REASON_CHARS], spend=0.0,
                                     kind="budget",
                                     claim_token=token_for(run_id, step_id),
                                     request_id=request_id(run_id, step_id, "over-budget")))
    except SystemExit as error:
        log(f"  {step_id}: over budget but could not be parked -- {error}")
        return False
    log(f"  {step_id}: not dispatched -- {reason}")
    return True
