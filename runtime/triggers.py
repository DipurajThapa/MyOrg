#!/usr/bin/env python3
"""Letting the world start work, without letting the world say what the work is.

Two things can wake this company up besides a person: a signed webhook from a system it
already trusts, and its own clock. Both go through the same narrow door -- they select a
*pre-registered* trigger and enqueue it. Neither supplies the goal text, because a payload
that could name its own goal would be an instruction from outside the trust boundary.

The HTTP path only enqueues. Planning is a model call and can take seconds or fail, so it
happens later, in the scheduler, where a failure is a retry rather than a dropped request.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402
from runtime.connectors import ConnectorError, WebhookVerifier  # noqa: E402
from runtime.db import Conflict, Store  # noqa: E402
from runtime.executor import ExecutorError  # noqa: E402
from runtime.backends import is_transient  # noqa: E402
from runtime.planner import plan  # noqa: E402

EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
DAILY_AT_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
MAX_TRIGGER_ATTEMPTS = 3
MAX_BODY_BYTES = 65_536
# Every queued trigger becomes a planned run, and planning costs money. A provider that
# retries in a loop -- or a signing key that leaks -- would otherwise turn an unbounded
# queue into an unbounded bill, with nobody at the keyboard to notice. The queue is
# therefore capped, and a full queue is a refusal the operator can see, not silent spend.
MAX_QUEUED_TRIGGERS = 50


class TriggerError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def next_fire(kind: str, after: datetime, interval_seconds: int | None = None,
              daily_at: str | None = None) -> datetime:
    """When this schedule is next due. Always strictly in the future, so a schedule that
    fell behind catches up once rather than firing for every interval it missed."""
    if kind == "interval":
        if not interval_seconds or interval_seconds < 60:
            raise TriggerError("interval schedules must be at least 60 seconds apart")
        return after + timedelta(seconds=interval_seconds)
    if kind == "daily":
        if not daily_at or not DAILY_AT_RE.fullmatch(daily_at):
            raise TriggerError("daily schedules need a HH:MM time of day in UTC")
        hour, minute = (int(part) for part in daily_at.split(":"))
        candidate = after.astimezone(timezone.utc).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate if candidate > after else candidate + timedelta(days=1)
    raise TriggerError(f"unknown schedule kind: {kind}")


def intake_id(source: str, source_ref: str) -> str:
    """One id per real-world event, so a replay lands on the row that already exists."""
    return f"tg-{hashlib.sha256(f'{source}:{source_ref}'.encode()).hexdigest()[:24]}"


def event_type_of(body: bytes) -> str:
    """The only field read out of an untrusted payload -- and it is used as a lookup key,
    never as text that reaches an agent."""
    if len(body) > MAX_BODY_BYTES:
        raise TriggerError("webhook payload exceeds the trigger limit")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise TriggerError("webhook body must be JSON") from error
    if not isinstance(payload, dict):
        raise TriggerError("webhook body must be a JSON object")
    value = str(payload.get("event_type", ""))
    if not EVENT_TYPE_RE.fullmatch(value):
        raise TriggerError("webhook event_type is missing or not a slug")
    return value


def receive_webhook(store: Store, org_id: str, connector_id: str, secret: bytes,
                    timestamp: str, nonce: str, signature: str, body: bytes) -> tuple[dict, bool]:
    """Verify, then enqueue. Nothing here plans, dispatches, or spends a token."""
    WebhookVerifier(store).verify(org_id, connector_id, secret, timestamp, nonce, body, signature)
    event_type = event_type_of(body)
    trigger = store.webhook_trigger(org_id, connector_id, event_type)
    if not trigger:
        raise TriggerError("no enabled trigger is registered for this connector and event type")
    return enqueue(store, org_id, intake_id("webhook", f"{connector_id}:{nonce}"),
                   "webhook", f"{connector_id}:{event_type}", trigger["goal"])


def enqueue(store: Store, org_id: str, identifier: str, source: str,
            source_ref: str, goal: str) -> tuple[dict, bool]:
    """Queue one piece of work, refusing once the backlog says nobody is keeping up."""
    depth = len(store.queued_triggers(org_id, MAX_QUEUED_TRIGGERS + 1))
    if depth > MAX_QUEUED_TRIGGERS:
        raise TriggerError(f"trigger queue is full ({depth} waiting); "
                           "work is arriving faster than it is being planned")
    return store.enqueue_trigger(org_id, identifier, source, source_ref, goal)


def fire_due_schedules(store: Store, org_id: str, now: datetime | None = None, log=print) -> list[dict]:
    """Enqueue every schedule the clock has passed. Claiming and enqueuing are separate, so a
    crash between them leaves a fired schedule with no work -- visible, and better than the
    reverse, which would start the same work twice."""
    moment = now or utc_now()
    fired = []
    for schedule in store.schedules(org_id):
        if not schedule["enabled"] or parse(schedule["next_fire_at"]) > moment:
            continue
        following = next_fire(schedule["kind"], moment, schedule["interval_seconds"], schedule["daily_at"])
        if not store.claim_due_schedule(org_id, schedule["id"], stamp(moment), stamp(following)):
            continue  # another sweeper took it
        try:
            row, created = enqueue(
                store, org_id, intake_id("schedule", f"{schedule['id']}:{schedule['next_fire_at']}"),
                "schedule", schedule["id"], schedule["goal"])
        except TriggerError as error:
            log(f"  schedule {schedule['id']}: {error}")
            continue
        if created:
            fired.append(row)
            log(f"  schedule {schedule['id']}: queued {row['id']}")
    return fired


def run_id_for(intake: dict) -> str:
    return f"run-{intake['id'][3:]}"


def start_queued(store: Store, org_id: str, backend, limit: int = 5, log=print) -> list[dict]:
    """Turn queued triggers into real runs. One failure never blocks the others, and a
    trigger that keeps failing is marked failed rather than retried forever."""
    started = []
    for intake in store.queued_triggers(org_id, limit):
        if intake["attempts"] >= MAX_TRIGGER_ATTEMPTS:
            # Keep the reason. Overwriting it with the count discarded the only useful
            # line -- an operator was left with "gave up after 3 attempts" and no why.
            store.settle_trigger(
                org_id, intake["id"], "failed", None,
                f"gave up after {intake['attempts']} attempts. Last error: "
                f"{(intake['last_error'] or 'unrecorded').strip()}")
            continue
        run_id = run_id_for(intake)
        try:
            # A run id is derived from the trigger, so finding one already there means a
            # previous attempt got as far as creating it and died before saying so. Adopt it
            # rather than refusing: refusing would burn this trigger's attempts against a run
            # that already exists and then abandon it with nothing pointing at it.
            if core.run_path(run_id).exists():
                _mark(store, org_id, intake["id"], "started", run_id, None)
                started.append({"intake_id": intake["id"], "run_id": run_id,
                                "source": intake["source"]})
                log(f"  trigger {intake['id']}: adopted the run a previous attempt created")
                continue
            # Say so *before* the call, not after. Planning happens inside the sweep and
            # ahead of the drive pass, and one model call can take minutes -- so during it
            # the log goes quiet, nothing else is driven, and an operator watching cannot
            # tell a company that is thinking from one that has died. The line costs
            # nothing and removes the ambiguity.
            log(f"  trigger {intake['id']}: planning (attempt {intake['attempts'] + 1} "
                f"of {MAX_TRIGGER_ATTEMPTS}) -- nothing else moves until this returns")
            costs: list[float] = []
            workflow = plan(intake["goal"], run_id, backend, log=lambda _message: None,
                            costs=costs)
            # Generated plans go next to the runs, never into `runtime/workflows/`. That
            # directory is hand-authored source under version control; a daemon writing
            # into it would grow the repository without bound and make `git status` noise
            # out of ordinary operation.
            destination = core.RUNS / f"{run_id}.planned.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
            # `create_run` is also a CLI command and prints the new run id; a daemon reports
            # through its own log, so the stray line is swallowed here.
            with redirect_stdout(io.StringIO()):
                core.create_run(SimpleNamespace(
                    workflow=str(destination), run_id=run_id, org=org_id, spend=sum(costs),
                    actor=f"trigger:{intake['source']}", request_id=f"trigger-{intake['id']}"))
        except (ExecutorError, TriggerError, SystemExit, OSError) as error:
            # A busy server is not this idea's fault, so it does not spend one of its three
            # chances. Without this a single overloaded minute exhausted the budget and the
            # request was abandoned permanently -- which is exactly what happened to one.
            transient = is_transient(str(error))
            log(f"  trigger {intake['id']}: {error}"
                + (" -- transient, will try again without spending an attempt"
                   if transient else ""))
            _mark(store, org_id, intake["id"], "queued", None, str(error),
                  count_attempt=not transient)
            continue
        _mark(store, org_id, intake["id"], "started", run_id, None)
        started.append({"intake_id": intake["id"], "run_id": run_id, "source": intake["source"]})
        log(f"  trigger {intake['id']}: started {run_id}")
    return started


def _mark(store: Store, org_id: str, intake_id_value: str, status: str,
          run_id: str | None, error: str | None, count_attempt: bool = True) -> None:
    """Settling is best-effort on the failure path: the run already exists or does not, and
    losing the bookkeeping must not also lose the run."""
    try:
        store.settle_trigger(org_id, intake_id_value, status, run_id, error, count_attempt)
    except (Conflict, ConnectorError):
        pass
