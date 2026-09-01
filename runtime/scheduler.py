#!/usr/bin/env python3
"""Keeps the company moving without anyone starting each run by hand.

Sweeps every run that can still move and drives it. Bounded on every axis -- passes,
wall-clock, and per-run work -- because an unattended loop with no ceiling is how an
autonomous system turns into a runaway one.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402
from runtime.executor import (BACKENDS, ClaudeCliBackend, ExecutorError,  # noqa: E402
                              MAX_ITERATIONS, StubBackend, advance)
from runtime.health import RUNNING, STALLED, all_health  # noqa: E402

DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_MAX_PASSES = 100
MOVABLE = {RUNNING, STALLED}


@dataclass
class SweepResult:
    """What one pass actually did, so an unattended loop stays accountable."""
    driven: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    @property
    def did_work(self) -> bool:
        return bool(self.driven or self.failed)

    def summary(self) -> str:
        parts = [f"drove {len(self.driven)}"]
        if self.failed:
            parts.append(f"{len(self.failed)} errored")
        parts.append(f"{len(self.skipped)} idle")
        return ", ".join(parts)


def movable_runs(now: datetime | None = None) -> list[str]:
    """Runs that can still progress on their own -- not finished, not waiting on a human."""
    return [run.run_id for run in all_health(now) if run.state in MOVABLE]


def sweep(backend, max_iterations: int = MAX_ITERATIONS, log=print) -> SweepResult:
    """One pass over every run that can move. One bad run never stops the others."""
    result = SweepResult()
    # Give back anything a dead worker was holding, before deciding what can move.
    from runtime.leases import reclaim
    reclaim(log=log)
    for run in all_health():
        if run.state not in MOVABLE:
            result.skipped.append(run.run_id)
            continue
        try:
            advance(run.run_id, backend, max_iterations=max_iterations, log=log)
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


def serve(backend, interval: int = DEFAULT_INTERVAL_SECONDS,
          max_passes: int = DEFAULT_MAX_PASSES, sleeper=time.sleep, log=print) -> int:
    """Sweep on an interval until nothing can move, or the pass budget runs out."""
    for completed in range(1, max_passes + 1):
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result = sweep(backend, log=log)
        log(f"pass {completed} at {stamp}: {result.summary()}")
        if not result.did_work:
            log("nothing left that can move on its own; stopping")
            return completed
        if completed < max_passes:
            sleeper(interval)
    log(f"reached the {max_passes}-pass ceiling; stopping")
    return max_passes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="one sweep, then exit")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--max-passes", type=int, default=DEFAULT_MAX_PASSES)
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="claude")
    parser.add_argument("--model")
    args = parser.parse_args(argv)

    if args.interval < 1 or args.max_passes < 1:
        print("interval and max-passes must be at least 1", file=sys.stderr)
        return 1
    backend = (ClaudeCliBackend(args.model) if args.backend == "claude"
               else StubBackend())
    if args.once:
        print(sweep(backend).summary())
        return 0
    try:
        serve(backend, args.interval, args.max_passes)
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
