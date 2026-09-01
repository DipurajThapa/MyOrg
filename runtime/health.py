#!/usr/bin/env python3
"""How every run is doing, in one glance.

Answers the three questions an operator actually has: what is moving, what is stuck on
me, and what has gone wrong. A run that is silently stalled is the dangerous case, so
stalled is its own state rather than a shade of "active".
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402
from runtime.executor import current_state  # noqa: E402

WAITING_STATUSES = {"awaiting_approval", "blocked_human"}
BLOCKED_PREFIX = "blocked_"
STALLED_AFTER_MINUTES = 30
RUNNING, WAITING, STALLED, FINISHED, FAILED = (
    "running", "waiting on you", "stalled", "finished", "failed")


def parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


@dataclass(frozen=True)
class RunHealth:
    run_id: str
    workflow_id: str
    goal: str
    state: str
    detail: str
    runtime_status: str
    done: int
    total: int
    cycles: int
    max_cycles: int
    idle_minutes: int | None
    waiting_on: tuple[str, ...] = ()

    @property
    def percent(self) -> int:
        return round(100 * self.done / self.total) if self.total else 0

    @property
    def needs_attention(self) -> bool:
        return self.state in (WAITING, STALLED, FAILED)


def idle_minutes(state: dict, now: datetime) -> int | None:
    stamped = parse_time(state.get("ts", ""))
    if stamped is None:
        return None
    return max(0, int((now - stamped).total_seconds() // 60))


def classify(state: dict, idle: int | None) -> tuple[str, str]:
    """A run's real condition, which is not always what run_status says."""
    status = state["run_status"]
    if status == "completed":
        return FINISHED, "All steps completed."
    if status in ("rejected", "rejected_by_checker"):
        return FAILED, f"Stopped: {status.replace('_', ' ')}."
    if status.startswith(BLOCKED_PREFIX):
        if status == "blocked_human":
            return WAITING, "A red step was handed back to you."
        return FAILED, f"Stopped: {status.replace('_', ' ')}."
    steps = state["steps"].values()
    parked = [s for s in steps if s["status"] in WAITING_STATUSES]
    if parked:
        return WAITING, f"{len(parked)} step(s) need your decision."
    if state["cycle_count"] >= state["max_cycles"]:
        return FAILED, "Out of cycle budget."
    movable = [s for s in steps
               if s["status"] in ("ready", "awaiting_check", "in_progress")]
    if not movable:
        return STALLED, "Nothing can move and nothing is waiting on you."
    if idle is not None and idle >= STALLED_AFTER_MINUTES:
        return STALLED, f"{len(movable)} step(s) ready but nothing has happened."
    return RUNNING, f"{len(movable)} step(s) in flight."


def unreadable(run_id: str, error: str) -> RunHealth:
    """A run whose own record is broken is the worst case, not an absent one."""
    return RunHealth(run_id=run_id, workflow_id="?", goal="?", state=FAILED,
                     detail=f"Run record is unreadable: {error}",
                     runtime_status="unreadable", done=0, total=0,
                     cycles=0, max_cycles=0, idle_minutes=None)


def health(run_id: str, now: datetime | None = None) -> RunHealth:
    try:
        state = current_state(run_id)
    except SystemExit as error:
        return unreadable(run_id, str(error))
    moment = now or datetime.now(timezone.utc)
    idle = idle_minutes(state, moment)
    condition, detail = classify(state, idle)
    steps = state["steps"]
    return RunHealth(
        run_id=run_id, workflow_id=state["workflow_id"], goal=state["goal"],
        state=condition, detail=detail, runtime_status=state["run_status"],
        done=sum(1 for s in steps.values() if s["status"] == "completed"),
        total=len(steps), cycles=state["cycle_count"], max_cycles=state["max_cycles"],
        idle_minutes=idle,
        waiting_on=tuple(sorted(sid for sid, s in steps.items()
                                if s["status"] in WAITING_STATUSES)))


def all_health(now: datetime | None = None) -> list[RunHealth]:
    """Everything, worst first -- an operator should never have to hunt for trouble."""
    order = {FAILED: 0, STALLED: 1, WAITING: 2, RUNNING: 3, FINISHED: 4}
    found = [health(path.stem, now) for path in core.run_files()]
    return sorted(found, key=lambda item: (order.get(item.state, 9), item.run_id))


def render(runs: list[RunHealth]) -> str:
    if not runs:
        return "No runs yet."
    lines = []
    for run in runs:
        flag = "!" if run.needs_attention else " "
        lines.append(f"{flag} {run.run_id:<24} {run.state:<14} "
                     f"{run.done}/{run.total} steps  {run.detail}")
    attention = sum(1 for run in runs if run.needs_attention)
    lines.append("")
    lines.append(f"{len(runs)} run(s); {attention} need attention.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    runs = [health(args.run_id)] if args.run_id else all_health()
    print(render([run for run in runs if run]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
