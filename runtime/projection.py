#!/usr/bin/env python3
"""The bridge between the company's two halves.

The JSONL event log is the system of record for execution -- append-only, hash-chained,
and never written to from here. The SQLite store holds identity, organizations and the
operator's read model. Before this, neither half had ever seen the other's data, and both
claimed to be "the runtime". This projects runs one way, log -> store, so the API and the
Control Center finally describe the same company the driver is actually running.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402
from runtime.db import Store, utc_now  # noqa: E402

DB_ENV = "MYORG_DB"


def default_db() -> Path:
    """Where the read model lives. Resolved per call, never cached at import.

    The store follows the runs it mirrors. `MYORG_RUNS_DIR` redirects the event log and the
    audit log; before this it did *not* redirect the projection target, so a test sweeping
    its own temporary runs still mirrored them into the company's real database -- ten
    fabricated `sch-*` runs were found in it. A read model is only trustworthy if nothing
    that is pretending can write to it, so the redirect now travels with the runs.
    """
    if os.environ.get(DB_ENV):
        return Path(os.environ[DB_ENV])
    runs = os.environ.get("MYORG_RUNS_DIR")
    if runs:
        return Path(runs) / "_read-model.db"
    return ROOT / "runtime" / "data" / "myorg.db"


RUNTIME_ACTOR = "runtime-projector"
# The store's own four-value contract; the precise runtime status rides alongside it.
COARSE_STATUS = {
    "active": "active",
    "completed": "completed",
    "rejected": "cancelled",
    "rejected_by_checker": "cancelled",
    "cancelled": "cancelled",
}
STEP_COLUMNS = ("owner", "checker", "action", "risk", "status", "attempts",
                "max_attempts", "review_cycles", "max_review_cycles", "depends_on",
                "evidence", "evidence_sha256", "approver", "approval_ref", "checked_by")


def coarse(runtime_status: str) -> str:
    """Map the runtime's precise vocabulary onto the store's older, narrower one."""
    return COARSE_STATUS.get(runtime_status,
                             "blocked" if runtime_status.startswith("blocked_") else "active")


def open_store(path: Path | None = None) -> Store:
    store = Store(path or default_db())
    store.migrate()
    return store


def ensure_org(store: Store, org_id: str) -> None:
    """A run may reach the store before anyone has registered its organization.

    Uses the store's own methods so its invariants -- role bindings, status checks --
    are enforced here exactly as they are for any other caller.
    """
    with store.reading() as connection:
        known = connection.execute(
            "SELECT 1 FROM organizations WHERE id=?", (org_id,)).fetchone()
    if not known:
        store.bootstrap_organization(org_id, org_id)
    store.upsert_actor(org_id, RUNTIME_ACTOR, "service", "Runtime projector",
                       ["chief-of-staff"])


def project_run(store: Store, run_id: str) -> dict | None:
    """Mirror one run's current state into the store. Idempotent by design."""
    try:
        state = core.read_events(run_id)[-1]
    except SystemExit:
        return None
    org_id = state.get("org_id") or core.DEFAULT_ORG
    ensure_org(store, org_id)
    moment = utc_now()
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO runs(id, org_id, workflow_id, workflow_revision, goal, "
            "data_class, status, created_by, created_at, updated_at, runtime_status, "
            "cycle_count, max_cycles, projected_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(org_id, id) DO UPDATE SET status=excluded.status, "
            "runtime_status=excluded.runtime_status, cycle_count=excluded.cycle_count, "
            "max_cycles=excluded.max_cycles, updated_at=excluded.updated_at, "
            "projected_at=excluded.projected_at",
            (run_id, org_id, state["workflow_id"], state["workflow_revision"],
             state["goal"][:500], "internal", coarse(state["run_status"]),
             RUNTIME_ACTOR, state.get("ts", moment), moment,
             state["run_status"], state["cycle_count"], state["max_cycles"], moment))
        for step_id, step in state["steps"].items():
            values = (org_id, run_id, step_id, step["owner"], step.get("checker"),
                      step["action"], step["risk"], step["status"], step["attempts"],
                      step["max_attempts"], step.get("review_cycles", 0),
                      step.get("max_review_cycles", 0),
                      json.dumps(step.get("depends_on", [])), step.get("evidence"),
                      step.get("evidence_sha256"), step.get("approver"),
                      step.get("approval_ref"), step.get("checked_by"), moment)
            assignments = ", ".join(f"{name}=excluded.{name}" for name in STEP_COLUMNS)
            connection.execute(
                "INSERT INTO run_steps(org_id, run_id, step_id, owner, checker, action, "
                "risk, status, attempts, max_attempts, review_cycles, max_review_cycles, "
                "depends_on, evidence, evidence_sha256, approver, approval_ref, "
                "checked_by, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(org_id, run_id, step_id) DO UPDATE SET "
                f"{assignments}, updated_at=excluded.updated_at", values)
    return {"run_id": run_id, "org_id": org_id, "status": state["run_status"],
            "steps": len(state["steps"])}


def project_all(store: Store | None = None, log=print) -> list[dict]:
    """Bring the store up to date with every run the driver knows about."""
    owned = store or open_store()
    projected = []
    for path in core.run_files():
        result = project_run(owned, path.stem)
        if result:
            projected.append(result)
    log(f"projected {len(projected)} run(s) into the store")
    return projected


def waiting_on_humans(store: Store, org_id: str) -> list[dict]:
    """The same question the approvals console answers, now askable over SQL."""
    with store.reading() as connection:
        rows = connection.execute(
            "SELECT run_id, step_id, owner, action, risk, status FROM run_steps "
            "WHERE org_id=? AND status IN ('awaiting_approval','blocked_human') "
            "ORDER BY run_id, step_id", (org_id,)).fetchall()
    return [dict(row) for row in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    store = open_store(args.db)
    if args.run_id:
        result = project_run(store, args.run_id)
        print(json.dumps(result) if result else f"unknown run: {args.run_id}")
        return 0 if result else 1
    for row in project_all(store):
        print(f"{row['org_id']}/{row['run_id']}\t{row['status']}\t{row['steps']} steps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
