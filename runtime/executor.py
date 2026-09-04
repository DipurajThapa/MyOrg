#!/usr/bin/env python3
"""The autonomous executor, and the one name every other module imports it by.

It comes apart along the direction its own calls already run: `executor_core` holds the
settings and the small helpers, `executor_grading` what a step is told and what happens when
the grader will not answer, `executor_steps` moving one step, `executor_drive` the loop over
all of them. Nothing calls back up that chain.

Everything is re-exported here because `from runtime import executor` is how the scheduler,
the agent API and thirteen test files reach it.
"""
from __future__ import annotations

import importlib

from runtime import executor_core as _core
from runtime import executor_drive as _drive
from runtime import executor_grading as _grading
from runtime import executor_steps as _steps

# Thirteen test files `importlib.reload` this module to pick up a changed MYORG_* value.
# Those values are read at import, in executor_core now, so reloading this module alone
# would re-import the old ones and the change would disappear without any error. Reload the
# sources first. The flag survives in this module's namespace across a reload, which is
# what tells this run apart from the first import.
if globals().get("_SOURCES_BOUND"):
    for _source in (_core, _grading, _steps, _drive):
        importlib.reload(_source)
_SOURCES_BOUND = True

from runtime.executor_core import (AGENTS_DIR, BACKENDS, CheckRequest, ClaudeCliBackend,
                                   EVIDENCE_DIR, ExecutorError, GRADE_ATTEMPTS,
                                   GRADE_BACKOFF_SECONDS, GRADE_PATTERN, GradeRequest, HALTED,
                                   HOLDER, Handoff, MAX_HANDOFF_CHARS, MAX_ITERATIONS,
                                   MAX_PARALLEL_STEPS, MAX_REASON_CHARS, MAX_SUBMISSION_CHARS,
                                   ROOT, STEP_TIMEOUT_SECONDS, StepRequest, StubBackend,
                                   VERDICTS, VERDICT_PATTERN, agent_brief, claim, clip, core,
                                   current_state, is_transient, namespace, parse_verdict,
                                   quietly, request_id, structural_failure, take, token_for,
                                   tools, write_evidence)
from runtime.executor_grading import (GraderUnavailable, acceptance_criteria,
                                      acceptance_failure, last_feedback, upstream_handoffs)
from runtime.executor_steps import (dispatch, finish, finish_approved_hold, hold_for_human,
                                    over_budget, record_failure, release_step, remembered_for,
                                    run_ceiling_usd)
from runtime.executor_drive import (advance, drive_check, drive_step, drive_together, main)

__all__ = [
    "AGENTS_DIR", "BACKENDS", "CheckRequest", "ClaudeCliBackend", "EVIDENCE_DIR",
    "ExecutorError", "GRADE_ATTEMPTS", "GRADE_BACKOFF_SECONDS", "GRADE_PATTERN",
    "GradeRequest", "GraderUnavailable", "HALTED", "HOLDER", "Handoff", "MAX_HANDOFF_CHARS",
    "MAX_ITERATIONS", "MAX_PARALLEL_STEPS", "MAX_REASON_CHARS", "MAX_SUBMISSION_CHARS",
    "ROOT", "STEP_TIMEOUT_SECONDS", "StepRequest", "StubBackend", "VERDICTS",
    "VERDICT_PATTERN", "acceptance_criteria", "acceptance_failure", "advance", "agent_brief",
    "claim", "clip", "core", "current_state", "dispatch", "drive_check", "drive_step",
    "drive_together", "finish", "finish_approved_hold", "hold_for_human", "is_transient",
    "last_feedback", "main", "namespace", "over_budget", "parse_verdict", "quietly",
    "record_failure", "release_step", "remembered_for", "request_id", "run_ceiling_usd",
    "structural_failure", "take", "token_for", "tools", "upstream_handoffs", "write_evidence",
]


if __name__ == "__main__":
    raise SystemExit(main())
