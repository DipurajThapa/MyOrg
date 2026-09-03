#!/usr/bin/env python3
"""How a step actually reaches an agent, and what to do when it does not.

Backends are duck-typed on the request: anything with `.prompt()`, `.brief` and `.kind`
works, which is what lets tests drive real runs without ever calling a model.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEP_TIMEOUT_SECONDS = 300

# A dispatched department gets the company's context, not the operator's laptop.
#
# `--strict-mcp-config` is the one that matters, and it is containment rather than economy.
# This repository ships no `.mcp.json`, so the only MCP servers a dispatch would inherit are
# whatever the person running it happens to have connected -- their mail, their calendar,
# their drive. A finance step reasoning about a budget has no business being handed somebody's
# inbox, and `tools.json` cannot take it away because MCP tools arrive outside that grant.
#
# `--disable-slash-commands` costs nothing to set: no grant in `tools.json` includes `Skill`,
# so a dispatched step already cannot invoke one. This stops paying to load what it may not
# use. Measured together at ~16% of each dispatch, with no change in output quality
# (docs/ARCHITECTURE-OPPORTUNITIES-2026-09-01.md §6.1).
DISPATCH_PROFILE = ("--strict-mcp-config", "--disable-slash-commands")


class ExecutorError(RuntimeError):
    """The driver could not make progress for a reason the runtime cannot record."""


class Output(str):
    """What an agent said, with what it cost to say it.

    A plain `str` everywhere it is already used -- every caller that treats a backend's
    return value as text keeps working untouched -- and carrying `cost_usd` for the one
    caller that now needs it. The alternative was returning a tuple and editing every call
    site, or hanging the figure off the backend instance, which would break the moment two
    runs were driven at once. This breaks in neither direction.
    """
    cost_usd: float = 0.0

    def __new__(cls, text: str, cost_usd: float = 0.0):
        value = super().__new__(cls, text)
        value.cost_usd = float(cost_usd)
        return value


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


# Failures that say "the other end is busy", not "your request is wrong". They are the one
# class where trying the identical thing later is the correct response -- so they must never
# be spent as repair attempts or counted against a permanent give-up cap.
TRANSIENT_MARKERS = (
    "529", "overloaded", "503", "service unavailable", "502", "bad gateway",
    "504", "gateway timeout", "429", "rate limit", "timed out", "timeout",
    "connection reset", "connection refused", "temporarily",
)


# Noise the CLI emits while shutting down, which is not why the dispatch failed. A session
# ending abruptly -- as it does the moment a model call errors -- cancels whatever hooks the
# operator's plugins registered, and those cancellations land on stderr. Reported first, as
# they were, they bury the real cause: an operator was told a plugin's SessionEnd hook had
# failed when the actual answer, further down the same message, was "529 Overloaded".
TEARDOWN_MARKERS = ("hook cancelled", "hook canceled", "sessionend hook", "session_end hook")


def is_teardown_noise(message: str) -> bool:
    """Whether a line is a side effect of the session ending rather than a cause of it."""
    lowered = message.lower()
    return any(marker in lowered for marker in TEARDOWN_MARKERS)


def is_transient(message: str) -> bool:
    """Whether a failure is the other end being busy rather than the request being wrong.

    A real 529 cost an operator their request: `plan()` fed the transport error back to the
    model as if it were malformed-JSON feedback, burning all three repair attempts inside one
    outage, and `start_queued` then counted that as a permanent attempt. Nine calls into an
    overloaded server, then the idea was thrown away -- for a fault that fixes itself.
    """
    return any(marker in message.lower() for marker in TRANSIENT_MARKERS)


def cli_failure(stdout: str, stderr: str, limit: int = 400) -> str:
    """Why the `claude` CLI exited non-zero, in words an operator can act on.

    Two things make this harder than reading stderr. First, with `--output-format json` the
    CLI's own failures go to *stdout* and stderr comes back empty -- reporting stderr alone
    produced `claude exited 1:` with nothing after the colon, which is how a planner failure
    became undiagnosable. Second, that JSON envelope leads with timings and token counts and
    puts `result`, `subtype` and `is_error` at the *end*, so slicing the first few hundred
    characters keeps the noise and drops the diagnosis. Pick the fields out instead, and only
    fall back to raw text when there is no envelope to read.
    """
    stdout, stderr = stdout.strip(), stderr.strip()
    parts = []
    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        envelope = None
    if isinstance(envelope, dict):
        for key in ("error", "result", "subtype", "stop_reason", "type"):
            value = envelope.get(key)
            if isinstance(value, dict):
                value = value.get("message") or json.dumps(value)
            if value not in (None, "", []):
                parts.append(f"{key}={str(value)[:limit]}")
        usage = envelope.get("usage") or {}
        if usage.get("input_tokens") == 0 and usage.get("output_tokens") == 0:
            # Nothing was sent and nothing came back: the CLI stopped before the model,
            # so look at the invocation -- flags, prompt size, session state -- not the model.
            parts.append("no tokens were used, so the CLI stopped before calling the model")
    elif stdout:
        parts.append(f"stdout={stdout[:limit]}")
    if stderr:
        # Shutdown noise goes last and is labelled as such; anything else on stderr is a
        # real cause and leads. Putting all of stderr first was what let a cancelled plugin
        # hook headline a failure whose actual reason was an overloaded API.
        if is_teardown_noise(stderr) and parts:
            parts.append(f"(also, while shutting down: {stderr[:limit]})")
        else:
            parts.insert(0, f"stderr={stderr[:limit]}")
    return " | ".join(parts) or "no output on either stream"


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
        # JSON rather than text, only so the CLI's own `total_cost_usd` comes back with the
        # answer. Nothing can count what a step spends by inspecting its prose.
        command = ["claude", "-p", request.prompt(), "--output-format", "json",
                   "--append-system-prompt", request.brief,
                   "--permission-mode", "dontAsk", *DISPATCH_PROFILE]
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
            raise ExecutorError(f"claude exited {result.returncode}: "
                                f"{cli_failure(result.stdout, result.stderr)}")
        output, cost = self._unpack(result.stdout)
        if not output:
            raise ExecutorError("agent returned no output")
        return Output(output + "\n", cost)

    @staticmethod
    def _unpack(raw: str) -> tuple[str, float]:
        """The deliverable and what it cost. A malformed envelope is a failed step, not a
        free one: reporting zero would let a broken parser look like thrift."""
        try:
            answer = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ExecutorError(f"claude returned unreadable JSON: {str(error)[:200]}") from error
        if answer.get("is_error"):
            raise ExecutorError(f"agent reported an error: {str(answer.get('result'))[:200]}")
        return str(answer.get("result", "")).strip(), float(answer.get("total_cost_usd") or 0.0)


BACKENDS = {"claude": ClaudeCliBackend, "stub": StubBackend}
