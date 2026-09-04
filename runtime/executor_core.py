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
