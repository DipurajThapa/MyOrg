#!/usr/bin/env python3
"""Independent review: a second department judging the first one's work.

The checker never edits the run. It reads the submission, returns a verdict, and the
runtime's own maker-checker commands apply it -- so "a different agent approved this" is
enforced by the state machine rather than by the driver's good manners.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402
from runtime.backends import ExecutorError  # noqa: E402
from runtime.prompts import (MAX_SUBMISSION_CHARS, CheckRequest,  # noqa: E402
                             VERDICT_PATTERN, agent_brief, clip, parse_verdict)

CHECK_COMMANDS = {"APPROVE": "check_approve", "RETURN": "check_return",
                  "REJECT": "check_reject"}
MIN_LESSON_CHARS = 25


def message_id(step_id: str) -> str:
    return f"chk-{step_id}-{uuid.uuid4().hex[:8]}"


def submission_text(step_id: str, step: dict) -> str:
    """The maker's artifact, re-hashed before the checker is allowed to see it."""
    try:
        relative, current_hash = core.evidence_path(step["evidence"])
    except (SystemExit, KeyError) as error:
        raise ExecutorError(f"submission for {step_id} is unreadable: {error}") from error
    if current_hash != step.get("evidence_sha256"):
        raise ExecutorError(f"submission for {step_id} changed after it was handed over")
    return clip((ROOT / relative).read_text(encoding="utf-8"), MAX_SUBMISSION_CHARS)


def send_verdict(run_id, step_id, step, verdict, review, quietly, namespace,
                 request_id) -> str:
    """File the checker's review as a typed message on the maker-checker edge."""
    identifier = message_id(step_id)
    quietly(core.send_message, namespace(
        run_id=run_id, step=step_id, message_id=identifier,
        from_agent=step["checker"], to_agent=step["owner"],
        kind="decision" if verdict == "APPROVE" else "feedback",
        subject=f"Check {verdict.lower()} for {step_id}"[:120],
        payload=review, classification="internal",
        reply_to=None, request_id=request_id(step_id, "verdict-message")))
    return identifier


def propose_lesson(run_id, step_id, step, checker, review, log) -> None:
    """A rejection is the company learning something. Offer it; a human decides."""
    from runtime.memory import propose
    headline = next((line.strip(" -*\t") for line in review.splitlines()[1:]
                     if len(line.strip(" -*\t")) > MIN_LESSON_CHARS), "")
    if not headline:
        return
    try:
        entry = propose(subject=f"{step['owner']} on {step['action']} work",
                        body=headline, author=checker, kind="lesson",
                        source_run=run_id, source_step=step_id)
    except SystemExit:
        return
    if entry:
        log(f"  {step_id}: proposed a lesson for your approval ({entry.id})")


def drive_check(run_id, step_id, state, backend, log, *,
                write_evidence, quietly, namespace, request_id) -> None:
    """Have the named checker independently review the maker's submission."""
    step = state["steps"][step_id]
    checker = step["checker"]
    try:
        output = backend(CheckRequest(
            run_id=run_id, step_id=step_id, agent=checker, maker=step["owner"],
            goal=state["goal"], brief=agent_brief(checker),
            submission=submission_text(step_id, step)))
    except ExecutorError as error:
        log(f"  {step_id}: checker {checker} could not review -- {error}")
        return
    verdict = parse_verdict(output)
    if not VERDICT_PATTERN.search(output):
        log(f"  {step_id}: checker gave no readable verdict -- treating as RETURN")
    # One file per review cycle: overwriting would break the hash pinned on the
    # earlier round's message.
    review = write_evidence(run_id, step_id, output,
                            label=f"check{step.get('review_cycles', 0) + 1}")
    try:
        identifier = send_verdict(run_id, step_id, step, verdict, review,
                                  quietly, namespace, request_id)
        result = quietly(getattr(core, CHECK_COMMANDS[verdict]), namespace(
            run_id=run_id, step=step_id, actor=checker,
            message_id=identifier, request_id=request_id(step_id, f"check-{verdict.lower()}")))
    except SystemExit as error:
        raise ExecutorError(f"could not record check on {step_id}: {error}") from error
    log(f"  {step_id}: {checker} says {verdict} -> {review} ({result})")
    if verdict != "APPROVE":
        propose_lesson(run_id, step_id, step, checker, output, log)
