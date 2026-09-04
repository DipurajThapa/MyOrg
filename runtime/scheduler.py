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

A pass lives in `scheduler_sweep`; this file is the service around it -- one instance at a
time, a clean stop, and the loop. Both halves are reachable from here.
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


import importlib

from runtime import scheduler_sweep as _sweep

# Tests set a MYORG_* value and reload this module. Those values are read at import, and
# they are read in scheduler_sweep now, so reload the source first or the change vanishes
# with no error. The flag survives a reload in this namespace; that is the signal.
if globals().get("_SOURCES_BOUND"):
    importlib.reload(_sweep)
_SOURCES_BOUND = True

from runtime.scheduler_sweep import (DEFAULT_INTERVAL_SECONDS, DEFAULT_MAX_PASSES, MOVABLE,
                                     ROOT, SweepResult, intake, mirror, movable_runs,
                                     org_suspended, run_org, sweep, trigger_store, watch)


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
    parser.add_argument("--log-file",
                        help="append every line here instead of the console -- for a service "
                             "with no window (pythonw, systemd)")
    args = parser.parse_args(argv)

    if args.log_file:
        # One redirect, not a logging framework: everything this process prints, including
        # a traceback, lands in the file. Line-buffered so a tail shows each pass as it ends.
        handle = open(args.log_file, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stderr = handle
        print(f"scheduler starting at {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
              f" pid={os.getpid()} args={' '.join(argv if argv is not None else sys.argv[1:])}")

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
