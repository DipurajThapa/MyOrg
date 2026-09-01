#!/usr/bin/env python3
"""How a step actually reaches an agent, and what to do when it does not.

Backends are duck-typed on the request: anything with `.prompt()`, `.brief` and `.kind`
works, which is what lets tests drive real runs without ever calling a model.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEP_TIMEOUT_SECONDS = 300


class ExecutorError(RuntimeError):
    """The driver could not make progress for a reason the runtime cannot record."""


class StubBackend:
    """Deterministic backend for tests. Never calls a model, never spends tokens."""

    def __call__(self, request) -> str:
        if request.kind == "brief":
            return (f"ASK: Allow {request.action} for {request.step_id}.\n"
                    "IF YES: The step runs and the run continues.\n"
                    "FINDINGS:\n- Upstream work completed.\n- No blockers recorded.\n"
                    "WATCH: This cannot be undone.\n"
                    "RECOMMEND: APPROVE - upstream checks passed.\n")
        if request.kind == "grade":
            return f"VERDICT: MEETS\n[stub] graded {request.step_id}\n"
        if request.kind == "check":
            return (f"VERDICT: APPROVE\n[stub] {request.agent} checked "
                    f"{request.step_id} submitted by {request.maker}\n")
        # Long enough to clear the structural gate -- a stub still has to look like a
        # deliverable, or the tests would only ever exercise the rejection path.
        return (f"[stub] {request.agent} completed {request.step_id} "
                f"(action={request.action}) for goal: {request.goal}\n"
                + f"Deliverable body for {request.step_id}, produced by "
                  f"{request.agent}. " * 6 + "\n")


class ClaudeCliBackend:
    """Dispatches a step to the owning agent through the local `claude` CLI.

    Tools are disabled: the agent returns text, and the driver is the only thing that
    writes to the run. That keeps every side effect inside the governed state machine.
    """

    def __init__(self, model: str | None = None, timeout: int = STEP_TIMEOUT_SECONDS):
        self.model = model
        self.timeout = timeout

    def __call__(self, request) -> str:
        # A request carries its own room and its own grant. Without them the agent gets
        # no tools at all, which is what grading and briefing want: they read, they do
        # not make. `dontAsk` is what turns an ungranted call into a refusal instead of a
        # prompt nobody is there to answer.
        room = getattr(request, "workspace", None)
        grant = getattr(request, "grant", None)
        command = ["claude", "-p", request.prompt(), "--output-format", "text",
                   "--append-system-prompt", request.brief,
                   "--permission-mode", "dontAsk"]
        if grant and room:
            command += ["--tools", ",".join(grant.tools), "--allowedTools", *grant.allow]
        else:
            command += ["--tools", "", "--allowedTools", ""]
        if self.model:
            command += ["--model", self.model]
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=self.timeout, cwd=room or ROOT, check=False)
        except FileNotFoundError as error:
            raise ExecutorError("`claude` CLI not found on PATH") from error
        except subprocess.TimeoutExpired as error:
            raise ExecutorError(f"step timed out after {self.timeout}s") from error
        if result.returncode != 0:
            raise ExecutorError(f"claude exited {result.returncode}: {result.stderr.strip()[:400]}")
        output = result.stdout.strip()
        if not output:
            raise ExecutorError("agent returned no output")
        return output + "\n"


BACKENDS = {"claude": ClaudeCliBackend, "stub": StubBackend}



BACKENDS = {"claude": ClaudeCliBackend, "stub": StubBackend}
