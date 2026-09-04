#!/usr/bin/env python3
"""The loop: what is ready, what runs together, and when a run has nothing left to do."""
from __future__ import annotations

from runtime.executor_core import (BACKENDS, ClaudeCliBackend, ExecutorError, HALTED, HOLDER,
                                   MAX_ITERATIONS, MAX_PARALLEL_STEPS, MAX_SUBMISSION_CHARS,
                                   StubBackend, ThreadPoolExecutor, _drive_check, argparse,
                                   as_completed, claim, core, current_state, is_transient, json,
                                   namespace, quietly, request_id, structural_failure, sys,
                                   take, tools, write_evidence)
from runtime.executor_grading import (acceptance_failure)
from runtime.executor_steps import (dispatch, finish, finish_approved_hold, hold_for_human,
                                    over_budget, record_failure, release_step)


def drive_together(work, step_ids: list[str], run_id: str, state: dict, backend, log) -> None:
    """Run this pass's independent steps at the same time.

    `depends_on` already says what may overlap: a step is `ready` only once everything it
    waits for has completed, so anything ready together is independent by construction. They
    were still driven one after another, and each spends minutes inside a model call, so a
    plan with three parallel branches took three times as long as the work required.

    Every state change still goes through `mutate` under the run lock; only the model calls
    overlap. Two accepted costs, both bounded:

    - The cost ceiling is read before a dispatch, so N steps starting together can each pass
      a check the others are about to invalidate. The overshoot is at most one pass of
      concurrent steps, and the ceiling parks the *next* pass -- it was always a soft stop.
    - One step's failure must not lose the others' work, so each is caught and reported
      exactly as it would have been in the loop this replaces.

    `MYORG_MAX_PARALLEL_STEPS` bounds it. 1 restores the old sequential behaviour exactly,
    which is what the tests with counting backends rely on.
    """
    if not step_ids:
        return
    width = max(1, min(MAX_PARALLEL_STEPS, len(step_ids)))
    if width == 1 or len(step_ids) == 1:
        for step_id in step_ids:
            work(run_id, step_id, state, backend, log)
        return
    lines: list[str] = []
    with ThreadPoolExecutor(max_workers=width) as pool:
        futures = {pool.submit(work, run_id, sid, state, backend, lines.append): sid
                   for sid in step_ids}
        for future in as_completed(futures):
            step_id = futures[future]
            try:
                future.result()
            except (ExecutorError, SystemExit) as error:
                lines.append(f"  {step_id}: stopped -- {error}")
    # One writer for the log, after the fact: `log` is often a list append or a file, and
    # neither is safe to call from several threads at once.
    for line in lines:
        log(line)


def drive_step(run_id: str, step_id: str, state: dict, backend, log) -> None:
    step = state["steps"][step_id]
    owner = step["owner"]
    if step["status"] == "in_progress":
        # Either a human approved it and handed it back, or another holder is mid-flight.
        if core.claim_is_live(step) and step.get("holder") != HOLDER:
            log(f"  {step_id}: held by {step['holder']} until {step['claim_expires_at']} -- left alone")
            return
        if step.get("holder") != HOLDER:
            try:
                take(run_id, step_id, owner)
            except SystemExit as error:
                log(f"  {step_id}: could not take -- {error}")
                return
            state = current_state(run_id)
            step = state["steps"][step_id]
        status = "in_progress"
    else:
        try:
            status = claim(run_id, step_id, owner)
        except SystemExit as error:
            raise ExecutorError(f"could not claim {step_id}: {error}") from error
    if status != "in_progress":
        # Park it with a brief a human can actually decide from, not a pile of prose.
        from runtime.briefing import write_brief
        written = write_brief(run_id, step_id, state, backend, MAX_SUBMISSION_CHARS)
        note = "brief written" if written else "no brief -- full work only"
        log(f"  {step_id}: {status} (risk={step['risk']}) -- left for a human, {note}")
        return
    if finish_approved_hold(run_id, step_id, step, state, log):
        return
    if over_budget(run_id, step_id, owner, state, log):
        return
    # Everything this dispatch costs, whatever it ends up doing. A rejected attempt is
    # still paid for, and grading is roughly 40% of the bill, so both are counted.
    costs: list[float] = []
    try:
        output, produced = dispatch(run_id, step_id, step, state, backend)
    except ExecutorError as error:
        # A busy server is not a failed attempt. `request_step` already spent one before the
        # call, so recording a failure here charged a step twice over for work nobody did:
        # a 529 returning zero tokens burned a step's whole budget and blocked the run.
        if is_transient(str(error)):
            log(f"  {step_id}: could not reach the agent -- {error}")
            release_step(run_id, step_id, owner, str(error), spend=sum(costs))
            return
        log(f"  {step_id}: agent failed -- {error}")
        record_failure(run_id, step_id, owner, str(error), spend=sum(costs))
        return
    costs.append(float(getattr(output, "cost_usd", 0.0)))
    # A human may have cancelled the run while the agent was working. The work is already
    # paid for; the grade is not. Skip it -- `finish` would be refused anyway.
    if current_state(run_id)["run_status"] != "active":
        log(f"  {step_id}: run ended while the agent was working -- output discarded")
        return
    rejection = structural_failure(output)
    if rejection is None:
        try:
            rejection = acceptance_failure(run_id, step_id, step, state, backend, output, costs)
        except ExecutorError as error:
            hold_for_human(run_id, step_id, owner, output, str(error), log, spend=sum(costs))
            return
    if rejection:
        log(f"  {step_id}: rejected -- {rejection}")
        record_failure(run_id, step_id, owner, rejection, spend=sum(costs))
        return
    # The reply and the files are one deliverable: the manifest goes inside the evidence,
    # so the hash the runtime records covers what was produced as well as what was said.
    evidence = write_evidence(run_id, step_id, output + "\n\n" + tools.manifest(produced))
    try:
        run_status = finish(run_id, step_id, owner, evidence, state["workflow_revision"],
                            spend=sum(costs))
    except SystemExit as error:
        raise ExecutorError(f"could not complete {step_id}: {error}") from error
    log(f"  {step_id}: completed by {owner} -> {evidence} (run={run_status})")


def advance(run_id: str, backend, max_iterations: int = MAX_ITERATIONS, log=print,
            halt=None) -> dict:
    """Drive the run until it finishes, needs a human, or stops making progress.

    `halt` is asked before every iteration; the scheduler passes "is this organization
    suspended". Work already dispatched in the current iteration finishes and records its
    own transition -- suspension stops the *next* dispatch, never a claim in flight."""
    for _ in range(max_iterations):
        if halt is not None and halt():
            log(f"run {run_id}: not driven -- the organization is suspended")
            return current_state(run_id)
        state = current_state(run_id)
        if state["run_status"] != "active":
            # REC-11: a terminal run used to be reported with its bare status, so an
            # operator re-driving one saw the same shape of line as a run that had just
            # done work. Say what it means and what can be done about it.
            recovery = {"blocked_cycle_limit":
                        " -- out of cycle budget; `extend-budget` to continue it",
                        "blocked_retry_limit":
                        " -- every attempt failed; the workflow needs changing, not retrying",
                        "blocked_review_limit":
                        " -- the checker kept returning it; a person should look",
                        "blocked_human":
                        " -- a red step was handed back; it is never automated",
                        }.get(state["run_status"], " -- nothing further will happen on its own")
            log(f"run {run_id}: {state['run_status']}{recovery}")
            return state
        # `in_progress` covers steps a human has just approved: claimed already, but
        # still needing the agent to do the work.
        # A step somebody else is holding is not work this driver can move. Leaving it in
        # the ready set would spin the loop until the iteration cap.
        elsewhere = sorted(sid for sid, s in state["steps"].items()
                           if core.claim_is_live(s) and s.get("holder") != HOLDER)
        ready = sorted(sid for sid, s in state["steps"].items()
                       if s["status"] in ("ready", "in_progress") and sid not in elsewhere)
        checks = sorted(sid for sid, s in state["steps"].items()
                        if s["status"] == "awaiting_check")
        if not ready and not checks:
            waiting = sorted(sid for sid, s in state["steps"].items() if s["status"] in HALTED)
            if elsewhere:
                log(f"run {run_id}: {len(elsewhere)} step(s) held by another worker: {elsewhere}")
            log(f"run {run_id}: no work left; waiting on a human for {waiting or 'nothing'}")
            return state
        log(f"run {run_id}: driving {len(ready)} step(s), {len(checks)} check(s)")
        drive_together(drive_step, ready, run_id, state, backend, log)
        # Checks run after the work of this pass, because a check reads what the pass
        # produced. Within that, they are independent of each other.
        drive_together(drive_check, checks, run_id, state, backend, log)
    raise ExecutorError(f"run {run_id} did not settle within {max_iterations} iterations")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="claude")
    parser.add_argument("--model")
    parser.add_argument("--max-iterations", type=int, default=MAX_ITERATIONS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    backend = ClaudeCliBackend(args.model) if args.backend == "claude" else StubBackend()
    try:
        state = advance(args.run_id, backend, args.max_iterations)
    except ExecutorError as error:
        print(f"executor stopped: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(state, indent=2, sort_keys=True))
    return 0 if state["run_status"] in {"active", "completed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())


def drive_check(run_id: str, step_id: str, state: dict, backend, log) -> None:
    """Run one independent review, lending `checking` the runtime helpers it needs."""
    # `checking` names its own mutations but should not have to know the run id to do it.
    _drive_check(run_id, step_id, state, backend, log,
                 write_evidence=write_evidence, quietly=quietly, namespace=namespace,
                 request_id=lambda step, verb: request_id(run_id, step, verb))
