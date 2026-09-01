#!/usr/bin/env python3
"""Autonomous driver for Company OS runs.

Turns ready steps into finished work without a human typing commands. Green steps are
dispatched to the owning agent and completed with the agent's output as evidence. Yellow
and red steps still stop exactly where they stopped before -- the driver never approves
anything, it only reports what is waiting.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import time
import uuid
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402
from runtime.backends import (BACKENDS, ClaudeCliBackend,  # noqa: E402,F401
                              ExecutorError, StubBackend, STEP_TIMEOUT_SECONDS)
from runtime.checking import drive_check as _drive_check  # noqa: E402
from runtime import tools  # noqa: E402
from runtime.prompts import (AGENTS_DIR, CheckRequest, GRADE_PATTERN,  # noqa: E402,F401
                             GradeRequest, Handoff, MAX_HANDOFF_CHARS,
                             MAX_SUBMISSION_CHARS, StepRequest, VERDICTS,
                             VERDICT_PATTERN, agent_brief, clip, parse_verdict,
                             structural_failure)

# `complete` only accepts evidence inside the repo, so evidence lives here whatever
# MYORG_RUNS_DIR points at.
EVIDENCE_DIR = ROOT / "runtime" / "runs"
MAX_ITERATIONS = 50
GRADE_ATTEMPTS = 3           # a grader blip must not cost a person's attention
GRADE_BACKOFF_SECONDS = 2    # multiplied by the attempt number
HALTED = {"awaiting_approval", "blocked_human"}
# One identity per driver process. Two drivers must be able to tell each other apart, and
# the role name cannot do that -- an outside worker acts as the same department.
HOLDER = f"executor-{uuid.uuid4().hex[:8]}"


def namespace(**fields) -> argparse.Namespace:
    """The runtime's commands take argparse namespaces; this lets code call them too."""
    return argparse.Namespace(**fields)


def quietly(function, args) -> str:
    """Run a runtime command, capturing the line it prints instead of echoing it."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        function(args)
    return buffer.getvalue().strip()


def current_state(run_id: str) -> dict:
    with core.run_lock(run_id):
        return core.read_events(run_id)[-1]


def write_evidence(run_id: str, step_id: str, text: str, label: str = "") -> str:
    """Persist an agent's output inside the repo, where the runtime can hash it."""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{run_id}.{step_id}{'.' + label if label else ''}.evidence"
    path = EVIDENCE_DIR / name
    path.write_text(text, encoding="utf-8")
    return str(path.resolve().relative_to(ROOT)).replace("\\", "/")


def request_id(run_id: str, step_id: str, verb: str) -> str:
    """A name for one mutation, derived from the work rather than from the clock.

    WF-13: this used to mint a uuid per call, so WF-04's idempotent replay could never fire
    on the autonomous path -- the same mutation applied twice looked like two different
    ones. Deriving it from (step, attempt, verb) means a driver that crashes and is swept
    again re-applies *the same* mutation, which the state machine then recognises and
    swallows.

    The verb is not decoration. Seven call sites share this function and each performs a
    different transition; without it, `claim` and `complete` on one attempt would collide
    and the second would be silently dropped as a replay of the first.

    What this does not fix: re-dispatching the model. The id protects the *write*, not the
    spend that preceded it -- that needs a record of the dispatch itself, which is A-01's
    territory, not this one.
    """
    try:
        attempt = current_state(run_id)["steps"][step_id].get("attempts", 0)
    except (SystemExit, KeyError):
        # A step that cannot be read has no attempt to name. Fall back to something unique
        # rather than something wrong: a collision here would swallow a real mutation.
        return f"exec-{step_id}-{uuid.uuid4().hex[:12]}"
    return f"exec-{step_id}-a{attempt}-{verb}"


def claim(run_id: str, step_id: str, owner: str) -> str:
    """Move a ready step into whatever the policy says comes next. Returns that status."""
    return quietly(core.request_step, namespace(
        run_id=run_id, step=step_id, actor=owner, holder=HOLDER,
        request_id=request_id(run_id, step_id, "claim")))


def take(run_id: str, step_id: str, owner: str) -> None:
    """Pick up an in-progress step nobody is holding -- the human-approved case."""
    quietly(core.take, namespace(run_id=run_id, step=step_id, actor=owner,
                                 holder=HOLDER, request_id=request_id(run_id, step_id, "take")))


def token_for(run_id: str, step_id: str) -> str | None:
    return current_state(run_id)["steps"][step_id].get("claim_token") or None


def upstream_handoffs(state: dict, step: dict) -> tuple[Handoff, ...]:
    """Evidence from the steps this one directly depends on.

    Only direct dependencies, matching the runtime's own rule that agents may only talk
    across adjacent DAG edges. Each artifact is re-hashed before it is trusted.
    """
    handoffs = []
    for dependency_id in step.get("depends_on", []):
        dependency = state["steps"].get(dependency_id, {})
        if dependency.get("status") != "completed" or not dependency.get("evidence"):
            continue
        try:
            relative, current_hash = core.evidence_path(dependency["evidence"])
        except SystemExit as error:
            raise ExecutorError(f"evidence for {dependency_id} is unreadable: {error}") from error
        if current_hash != dependency.get("evidence_sha256"):
            raise ExecutorError(f"evidence for {dependency_id} changed after it was recorded")
        text = (ROOT / relative).read_text(encoding="utf-8")
        handoffs.append(Handoff(dependency_id, dependency["owner"], clip(text)))
    return tuple(handoffs)


def last_feedback(state: dict, step: dict) -> str:
    """What the checker said when it sent this step back. Without it the maker is
    blind and repeats the same mistake until it hits the review limit."""
    identifier = step.get("last_feedback_message")
    if not identifier:
        # A plain failure (agent error or a rejected deliverable) leaves its reason here.
        return step.get("last_failure", "")
    message = next((m for m in state.get("messages", []) if m["id"] == identifier), None)
    if not message:
        return ""
    try:
        relative, current_hash = core.evidence_path(message["payload"])
    except SystemExit:
        return ""
    if current_hash != message.get("payload_sha256"):
        return ""
    return clip((ROOT / relative).read_text(encoding="utf-8"))


def acceptance_criteria(run_id: str, step_id: str) -> tuple[str, ...]:
    """Criteria declared on the step in the workflow snapshot taken at run creation."""
    snapshot = core.RUNS / f"{run_id}.workflow.json"
    if not snapshot.is_file():
        return ()
    workflow = json.loads(snapshot.read_text(encoding="utf-8"))
    for step in workflow.get("steps", []):
        if step.get("id") == step_id:
            criteria = step.get("acceptance") or ()
            return tuple(str(c) for c in criteria)
    return ()


class GraderUnavailable(ExecutorError):
    """The acceptance check could not be run at all -- which is not the same as a pass."""


def acceptance_failure(run_id, step_id, step, state, backend, output) -> str | None:
    """Ask an independent grader whether the work actually meets its criteria.

    A grader that cannot answer is retried a few times, because a blip should not cost a
    person's attention. If it still cannot answer, this raises: a control that did not run
    must never be recorded as one that passed."""
    criteria = acceptance_criteria(run_id, step_id)
    if not criteria:
        return None
    request = GradeRequest(
        step_id=step_id, agent=step["owner"], goal=state["goal"],
        brief=agent_brief(step["owner"]), criteria=criteria,
        deliverable=clip(output, MAX_SUBMISSION_CHARS))
    last = None
    for attempt in range(1, GRADE_ATTEMPTS + 1):
        try:
            verdict = backend(request)
        except ExecutorError as error:
            last = error
            if attempt < GRADE_ATTEMPTS:
                time.sleep(GRADE_BACKOFF_SECONDS * attempt)
            continue
        found = GRADE_PATTERN.search(verdict)
        if found and found.group(1).upper() == "MEETS":
            return None
        return f"did not meet acceptance criteria: {verdict.strip()[:600]}"
    raise GraderUnavailable(f"{last} (after {GRADE_ATTEMPTS} attempts)")


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
    output = backend(StepRequest(run_id=run_id, step_id=step_id, agent=owner,
                                 action=step["action"], goal=state["goal"],
                                 brief=agent_brief(owner),
                                 handoffs=upstream_handoffs(state, step),
                                 feedback=last_feedback(state, step),
                                 remembered=remembered_for(step_id, step, state),
                                 workspace=room, grant=grant))
    return output, tools.produced_files(room)


def finish(run_id: str, step_id: str, owner: str, evidence: str, revision: str) -> str:
    return quietly(core.complete, namespace(
        run_id=run_id, step=step_id, actor=owner, evidence=evidence,
        revision=revision, claim_token=token_for(run_id, step_id),
        request_id=request_id(run_id, step_id, "complete")))


def record_failure(run_id: str, step_id: str, owner: str, reason: str) -> None:
    """Hand the failure to the state machine so its retry budget decides what happens."""
    try:
        quietly(core.fail, namespace(run_id=run_id, step=step_id, actor=owner,
                                     reason=reason[:200],
                                     claim_token=token_for(run_id, step_id),
                                     request_id=request_id(run_id, step_id, "fail")))
    except SystemExit as error:
        raise ExecutorError(f"could not record failure on {step_id}: {error}") from error


def hold_for_human(run_id: str, step_id: str, owner: str, output: str,
                   reason: str, log) -> None:
    """Keep the work, park the step, tell a person. Never pass what was not graded."""
    artifact = write_evidence(run_id, step_id, output, "ungraded")
    try:
        quietly(core.hold, namespace(run_id=run_id, step=step_id, actor=owner,
                                     evidence=artifact, reason=reason[:200],
                                     claim_token=token_for(run_id, step_id),
                                     request_id=request_id(run_id, step_id, "hold")))
    except SystemExit as error:
        raise ExecutorError(f"could not hold {step_id}: {error}") from error
    log(f"  {step_id}: quality gate could not run -- {reason}; held for a human ({artifact})")


def finish_approved_hold(run_id: str, step_id: str, step: dict, state: dict, log) -> bool:
    """A human approved work whose gate could not run. Use it -- do not produce it again."""
    held = step.get("held_evidence")
    if not held:
        return False
    try:
        run_status = finish(run_id, step_id, step["owner"], held, state["workflow_revision"])
    except SystemExit as error:
        raise ExecutorError(f"could not complete {step_id}: {error}") from error
    log(f"  {step_id}: completed with human-approved ungraded work -> {held} (run={run_status})")
    return True


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
    try:
        output, produced = dispatch(run_id, step_id, step, state, backend)
    except ExecutorError as error:
        log(f"  {step_id}: agent failed -- {error}")
        record_failure(run_id, step_id, owner, str(error))
        return
    rejection = structural_failure(output)
    if rejection is None:
        try:
            rejection = acceptance_failure(run_id, step_id, step, state, backend, output)
        except ExecutorError as error:
            hold_for_human(run_id, step_id, owner, output, str(error), log)
            return
    if rejection:
        log(f"  {step_id}: rejected -- {rejection}")
        record_failure(run_id, step_id, owner, rejection)
        return
    # The reply and the files are one deliverable: the manifest goes inside the evidence,
    # so the hash the runtime records covers what was produced as well as what was said.
    evidence = write_evidence(run_id, step_id, output + "\n\n" + tools.manifest(produced))
    try:
        run_status = finish(run_id, step_id, owner, evidence, state["workflow_revision"])
    except SystemExit as error:
        raise ExecutorError(f"could not complete {step_id}: {error}") from error
    log(f"  {step_id}: completed by {owner} -> {evidence} (run={run_status})")


def advance(run_id: str, backend, max_iterations: int = MAX_ITERATIONS, log=print) -> dict:
    """Drive the run until it finishes, needs a human, or stops making progress."""
    for _ in range(max_iterations):
        state = current_state(run_id)
        if state["run_status"] != "active":
            log(f"run {run_id}: {state['run_status']}")
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
        for step_id in ready:
            drive_step(run_id, step_id, state, backend, log)
        for step_id in checks:
            drive_check(run_id, step_id, state, backend, log)
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
