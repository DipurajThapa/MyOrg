#!/usr/bin/env python3
"""Keeps the company moving, and now starts it moving, without anyone typing a command.

Each pass takes in whatever the world or the clock has queued, then drives every run that
can still move.

Bounded on every axis that matters -- per pass, per run, and per step -- because an
unattended loop with no ceiling is how an autonomous system turns into a runaway one. As a
*service* (`--supervised`) the pass *count* is deliberately unbounded: an idle pass means the
company is waiting for a trigger, not that its work is finished, and a loop that stopped
there would end autonomy until somebody opened a terminal. The ceilings that prevent a
runaway are the ones inside the state machine, not a limit on how long the process lives.
Only one supervised loop may hold a runs directory at a time.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402
from runtime.executor import (BACKENDS, ClaudeCliBackend, ExecutorError,  # noqa: E402
                              MAX_ITERATIONS, StubBackend, advance)
from runtime.health import RUNNING, STALLED, all_health  # noqa: E402
from runtime.planner import StubPlannerBackend  # noqa: E402

DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_MAX_PASSES = 100
MOVABLE = {RUNNING, STALLED}


@dataclass
class SweepResult:
    """What one pass actually did, so an unattended loop stays accountable."""
    driven: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    started: list[str] = field(default_factory=list)

    @property
    def did_work(self) -> bool:
        return bool(self.driven or self.failed or self.started)

    def summary(self) -> str:
        parts = []
        if self.started:
            parts.append(f"started {len(self.started)}")
        parts.append(f"drove {len(self.driven)}")
        if self.failed:
            parts.append(f"{len(self.failed)} errored")
        parts.append(f"{len(self.skipped)} idle")
        return ", ".join(parts)


def movable_runs(now: datetime | None = None) -> list[str]:
    """Runs that can still progress on their own -- not finished, not waiting on a human."""
    return [run.run_id for run in all_health(now) if run.state in MOVABLE]


def trigger_store():
    """The store, only if one already exists. Triggers live in the database, so a log-only
    installation has none -- and must not have one conjured for it by the driver."""
    from runtime.projection import DB_ENV, default_db
    from runtime.db import Store
    path = Path(os.environ[DB_ENV]) if os.environ.get(DB_ENV) else default_db()
    return Store(path) if path.is_file() else None


def org_suspended(org_id: str) -> bool:
    """Suspended means the tenant is off: no intake, no webhooks, no tokens -- and no
    driving. Read fresh every time it is asked, because the point of suspension is that it
    takes effect while a pass is already under way. A log-only install has no store and
    therefore no suspension."""
    try:
        store = trigger_store()
        return store is not None and store.organization_status(org_id) == "suspended"
    except Exception:  # noqa: BLE001 - an unreadable store must not stop the company
        return False


def run_org(run_id: str) -> str:
    try:
        return core.read_events(run_id)[-1].get("org_id", "")
    except SystemExit:
        return ""


def intake(planner_backend, log=print) -> list[dict]:
    """Fire whatever the clock is due, then turn queued triggers into runs.

    This is the half of autonomy that *starts* work rather than finishing it. It runs
    before the drive pass so a run created here moves in the same sweep. Like projection
    and escalation it must never stop the driver: a company that cannot plan new work
    should still finish the work it already has.
    """
    try:
        store = trigger_store()
        if store is None:
            return []
        from runtime import triggers
        org = os.environ.get("MYORG_ORG_ID", "default")
        if store.organization_status(org) != "active":
            # Suspended means suspended (B-03): nothing new starts, nothing is driven. The
            # watchers still watch -- a paused company must never look like a quiet one.
            log(f"  intake skipped: organization {org} is suspended")
            return []
        triggers.fire_due_schedules(store, org, log=log)
        return triggers.start_queued(store, org, planner_backend, log=log)
    except Exception as error:  # noqa: BLE001 - starting work must not stop finishing it
        log(f"  intake skipped: {error}")
        return []


def sweep(backend, max_iterations: int = MAX_ITERATIONS, log=print,
          planner_backend=None) -> SweepResult:
    """One pass: take in new work, then move every run that can move. One bad run never
    stops the others. Without a planner backend nothing new is started -- the sweep drives
    what already exists, which is what every caller before triggers existed expected."""
    result = SweepResult()
    # A worker that stopped heartbeating simply lets its claim expire; `drive_step` then
    # adopts the step. There is no second liveness record to sweep (B-01).
    if planner_backend is not None:
        result.started = [item["run_id"] for item in intake(planner_backend, log)]
    for run in all_health():
        if run.state not in MOVABLE:
            result.skipped.append(run.run_id)
            continue
        org = run_org(run.run_id)
        if org_suspended(org):
            result.skipped.append(run.run_id)
            continue
        try:
            advance(run.run_id, backend, max_iterations=max_iterations, log=log,
                    halt=lambda org=org: org_suspended(org))
            result.driven.append(run.run_id)
        except (ExecutorError, SystemExit) as error:
            result.failed[run.run_id] = str(error)
            log(f"  {run.run_id}: stopped -- {error}")
    mirror(log)
    watch(log)
    return result


def watch(log=print) -> None:
    """Raise a notice for anything that needs a person. Never blocks the sweep."""
    try:
        from runtime.escalation import scan
        scan(log=log)
    except Exception as error:  # noqa: BLE001 - escalation must not stop the driver
        log(f"  escalation skipped: {error}")


def mirror(log=print) -> None:
    """Keep the store's read model level with the log. Never blocks the sweep."""
    from runtime.projection import DB_ENV, default_db
    if DB_ENV not in os.environ and not default_db().is_file():
        return  # no store configured; the log alone is the system of record
    try:
        from runtime.projection import project_all
        project_all(log=lambda _message: None)
    except Exception as error:  # noqa: BLE001 - the driver must run without a database
        log(f"  projection skipped: {error}")


class AlreadyRunning(RuntimeError):
    """Another supervised loop holds the company. Two would double every model call."""


@contextmanager
def single_instance(enabled: bool = True):
    """One sweeper per runs directory.

    Steps are already fenced (REC-10) and schedules are fenced by `next_fire_at`, so a
    second loop cannot corrupt state -- but it can still plan the same goal twice, pay for
    the same step twice, and fill the log with two of everything. The common way to get
    two is mundane: a systemd unit restarting while an operator has one open in a terminal.
    """
    if not enabled:
        yield
        return
    from runtime.filelock import exclusive_lock
    lock = core.RUNS / "_scheduler.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        with exclusive_lock(lock, timeout=0.5):
            yield
    except SystemExit as error:
        # `exclusive_lock` reports a timeout by exiting; here it means somebody else has it.
        raise AlreadyRunning(
            f"another supervised scheduler already holds {lock}; refusing to run a second"
        ) from error


class Shutdown:
    """A stop that finishes the pass it is in. Killing a sweep mid-run would leave a step
    claimed by a process that no longer exists, so SIGTERM sets a flag instead."""

    def __init__(self):
        self.requested = False

    def request(self, *_args) -> None:
        self.requested = True

    def install(self) -> None:
        for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
            handler = getattr(signal, name, None)
            if handler is not None:
                signal.signal(handler, self.request)


def serve(backend, interval: int = DEFAULT_INTERVAL_SECONDS,
          max_passes: int = DEFAULT_MAX_PASSES, sleeper=time.sleep, log=print,
          stop_when_idle: bool = True, shutdown: Shutdown | None = None,
          planner_backend=None) -> int:
    """Sweep on an interval.

    As a *command* it stops once nothing can move, which is what an operator at a keyboard
    wants. As a *service* (`stop_when_idle=False`) an idle pass is the normal state -- the
    company is waiting for a trigger, not finished -- so it keeps waiting. `max_passes=0`
    lifts the pass ceiling for that supervised case; the per-pass and per-run ceilings stay,
    so no single pass can run away.
    """
    completed = 0
    if not stop_when_idle and not os.environ.get("MYORG_NOTIFY_COMMAND", "").strip():
        # The one thing an unattended loop must not be is silent about being silent.
        log("WARNING: MYORG_NOTIFY_COMMAND is not set -- notices will pile up in the outbox "
            "and nobody will be told. See docs/OPERATIONS-RUNBOOK.md#being-told")
    while max_passes == 0 or completed < max_passes:
        if shutdown and shutdown.requested:
            log(f"stop requested; stopping cleanly after {completed} passes")
            return completed
        completed += 1
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result = sweep(backend, log=log, planner_backend=planner_backend)
        log(f"pass {completed} at {stamp}: {result.summary()}")
        if not result.did_work and stop_when_idle:
            log("nothing left that can move on its own; stopping")
            return completed
        if max_passes == 0 or completed < max_passes:
            sleeper(interval)
    log(f"reached the {max_passes}-pass ceiling; stopping")
    return completed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Notices (a decision waiting, a run that stopped) go to the outbox and are "
               "delivered by the command in MYORG_NOTIFY_COMMAND; unset, nobody is told. "
               "See docs/OPERATIONS-RUNBOOK.md#being-told.")
    parser.add_argument("--once", action="store_true", help="one sweep, then exit")
    parser.add_argument("--supervised", action="store_true",
                        help="run as a service: an idle pass is normal, stop only on a signal")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--max-passes", type=int, default=DEFAULT_MAX_PASSES)
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="claude")
    parser.add_argument("--no-intake", action="store_true",
                        help="drive existing runs only; start nothing new")
    parser.add_argument("--model")
    args = parser.parse_args(argv)

    if args.interval < 1 or args.max_passes < 1:
        print("interval and max-passes must be at least 1", file=sys.stderr)
        return 1
    live = args.backend == "claude"
    backend = ClaudeCliBackend(args.model) if live else StubBackend()
    # Planning a triggered goal is a different job from running a step, so it gets its own
    # backend -- and `--no-intake` exists because an operator sweeping by hand usually wants
    # to finish what is there, not start anything new.
    planner_backend = None if args.no_intake else (
        ClaudeCliBackend(args.model) if live else StubPlannerBackend())
    if args.once:
        print(sweep(backend, planner_backend=planner_backend).summary())
        return 0
    stopper = Shutdown()
    if args.supervised:
        stopper.install()
    try:
        with single_instance(args.supervised):
            serve(backend, args.interval, 0 if args.supervised else args.max_passes,
                  stop_when_idle=not args.supervised, shutdown=stopper,
                  planner_backend=planner_backend)
    except AlreadyRunning as error:
        print(str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
