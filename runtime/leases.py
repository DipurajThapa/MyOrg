#!/usr/bin/env python3
"""Who currently holds a step, and what happens when they go quiet.

A step moves to `in_progress` the moment it is claimed. Without a lease, a worker that
crashes there strands that step forever and the run silently stops. A lease turns
"someone is working on this" into a claim that expires unless it is renewed.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402
from runtime.filelock import exclusive_lock  # noqa: E402

LEASE_SECONDS = int(os.environ.get("MYORG_LEASE_SECONDS", "600"))


def leases_path() -> Path:
    return core.RUNS / "leases.json"


def parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Lease:
    run_id: str
    step_id: str
    agent: str
    expires_at: str

    @property
    def key(self) -> str:
        return f"{self.run_id}/{self.step_id}"

    def expired(self, now: datetime) -> bool:
        deadline = parse_time(self.expires_at)
        return deadline is None or deadline <= now


def read_all() -> dict[str, Lease]:
    path = leases_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {key: Lease(**value) for key, value in raw.items()}


def write_all(leases: dict[str, Lease]) -> None:
    path = leases_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: vars(v) for k, v in leases.items()},
                               indent=2, sort_keys=True), encoding="utf-8")


def grant(run_id: str, step_id: str, agent: str,
          seconds: int = LEASE_SECONDS, now: datetime | None = None) -> Lease:
    """Hand this step to one worker for a bounded time."""
    moment = now or datetime.now(timezone.utc)
    lease = Lease(run_id, step_id, agent,
                  stamp(moment + timedelta(seconds=seconds)))
    with exclusive_lock(core.RUNS / "leases.lock"):
        leases = read_all()
        leases[lease.key] = lease
        write_all(leases)
    return lease


def renew(run_id: str, step_id: str, agent: str,
          seconds: int = LEASE_SECONDS, now: datetime | None = None) -> Lease:
    """A heartbeat. Only the holder may renew -- otherwise it is a takeover."""
    moment = now or datetime.now(timezone.utc)
    with exclusive_lock(core.RUNS / "leases.lock"):
        leases = read_all()
        held = leases.get(f"{run_id}/{step_id}")
        if held is None:
            raise SystemExit(f"no lease held on {run_id}/{step_id}")
        if held.agent != agent:
            raise SystemExit(f"{step_id} is held by {held.agent}, not {agent}")
        lease = Lease(run_id, step_id, agent,
                      stamp(moment + timedelta(seconds=seconds)))
        leases[lease.key] = lease
        write_all(leases)
    return lease


def release(run_id: str, step_id: str) -> None:
    with exclusive_lock(core.RUNS / "leases.lock"):
        leases = read_all()
        leases.pop(f"{run_id}/{step_id}", None)
        write_all(leases)


def held_by(run_id: str, step_id: str) -> Lease | None:
    return read_all().get(f"{run_id}/{step_id}")


def abandoned(now: datetime | None = None) -> list[Lease]:
    """Steps still marked in-progress whose holder stopped answering."""
    moment = now or datetime.now(timezone.utc)
    lost = []
    for lease in read_all().values():
        if not lease.expired(moment):
            continue
        try:
            state = core.read_events(lease.run_id)[-1]
        except SystemExit:
            continue
        step = state["steps"].get(lease.step_id, {})
        if state["run_status"] == "active" and step.get("status") == "in_progress":
            lost.append(lease)
    return lost


def reclaim(now=None, log=print) -> list[str]:
    """Give abandoned work back to the runtime, which decides retry or give up."""
    from runtime.executor import namespace, quietly, request_id
    recovered = []
    for lease in abandoned(now):
        try:
            quietly(core.fail, namespace(
                run_id=lease.run_id, step=lease.step_id, actor=lease.agent,
                reason=f"lease expired -- {lease.agent} stopped responding",
                request_id=request_id(lease.step_id)))
        except SystemExit as error:
            log(f"  could not reclaim {lease.key}: {error}")
            continue
        release(lease.run_id, lease.step_id)
        recovered.append(lease.key)
        log(f"  reclaimed {lease.key} from {lease.agent}")
    return recovered
