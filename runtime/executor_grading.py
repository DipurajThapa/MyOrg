#!/usr/bin/env python3
"""What a step is told before it works, and what happens when the grader will not answer."""
from __future__ import annotations

from runtime.executor_core import (ExecutorError, GRADE_ATTEMPTS, GRADE_BACKOFF_SECONDS,
                                   GRADE_PATTERN, GradeRequest, Handoff, MAX_SUBMISSION_CHARS,
                                   ROOT, agent_brief, clip, core, json, time, tools)


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
