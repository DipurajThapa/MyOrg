#!/usr/bin/env python3
"""One pass: what is movable, what the world asked for, and driving each run once.

Every part of a pass is wrapped so one run's failure cannot end the pass. That isolation
is the whole point of this file, and it is easier to check with the daemon next door.
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

