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
import os
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402
from runtime.backends import (BACKENDS, ClaudeCliBackend,  # noqa: E402,F401
                              ExecutorError, StubBackend, STEP_TIMEOUT_SECONDS,
                              is_transient)
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
# How much of a rejection survives into the run. This was 200 characters, which cut the
# grader off mid-sentence -- and the same text is what the *next attempt* is given as
# feedback, so a step was being told to fix something the instruction no longer named. A
# real run failed three times reading "Criterion 3 misses. What criterion 3 misses 1.".
# Bounded, because the reason rides in every later event of the run log.
MAX_REASON_CHARS = 2000
# How many independent steps of one pass may be in a model call at once.
#
# The default is 1, and that is a governance decision rather than caution. A spend ceiling is
# read *before* a dispatch and the spend is recorded *after* it, so steps that start together
# all pass a check each of them is about to invalidate -- with several independent steps the
# ceiling stops binding within a pass at all. Concurrency cannot be reconciled with an exact
# ceiling without reserving budget against an estimate nobody has.
#
# So the speed is available and opted into, not assumed: raise this where wall-clock matters
# more than an exact stop, and the overshoot is bounded by what one pass of concurrent steps
# costs. `MYORG_RUN_CEILING_USD=0` disables the ceiling entirely, which is the honest pairing
# for a high setting here.
MAX_PARALLEL_STEPS = max(1, int(os.environ.get('MYORG_MAX_PARALLEL_STEPS', '1')))
GRADE_ATTEMPTS = 3           # a grader blip must not cost a person's attention
GRADE_BACKOFF_SECONDS = 2    # multiplied by the attempt number
HALTED = core.WAITING_STEP
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
        step = current_state(run_id)["steps"][step_id]
    except (SystemExit, KeyError):
        # A step that cannot be read has no attempt to name. Fall back to something unique
        # rather than something wrong: a collision here would swallow a real mutation.
        return f"exec-{step_id}-{uuid.uuid4().hex[:12]}"
    # Releases are counted here as well as attempts, because a release puts the attempt
    # number *back*. Keyed on attempts alone, the claim after a release rebuilt the claim
    # before it, `mutate` swallowed it as a replay, and the step sat `ready` being
    # re-dispatched forever -- a live run spun forty passes that way. Both counters only
    # ever move, so a genuine crash-replay of the same mutation still dedupes.
    return f"exec-{step_id}-a{step.get('attempts', 0)}-r{step.get('releases', 0)}-{verb}"


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


def acceptance_failure(run_id, step_id, step, state, backend, output,
                       costs: list | None = None) -> str | None:
    """Ask an independent grader whether the work actually meets its criteria.

    A grader that cannot answer is retried a few times, because a blip should not cost a
    person's attention. If it still cannot answer, this raises: a control that did not run
    must never be recorded as one that passed.

    `costs` collects what each grading call cost. Grading was measured at roughly 40% of a
    step's bill, so a spend figure that counts only the dispatch is wrong by nearly half --
    and the retries above are the expensive case, not the cheap one."""
    criteria = acceptance_criteria(run_id, step_id)
    if not criteria:
        return None
    request = GradeRequest(
        step_id=step_id, agent=step["owner"], goal=state["goal"],
        brief=agent_brief(step["owner"]), criteria=criteria,
        deliverable=clip(output, MAX_SUBMISSION_CHARS),
        author_could_search=tools.reaches_outward(tools.grant_for(step["owner"])),
        # Straight from the dispatch that produced this text, never re-derived from it.
        retrieved=getattr(output, "retrieved", ()))
    last = None
    for attempt in range(1, GRADE_ATTEMPTS + 1):
        try:
            verdict = backend(request)
            if costs is not None:
                costs.append(float(getattr(verdict, "cost_usd", 0.0)))
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
