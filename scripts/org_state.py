#!/usr/bin/env python3
"""Small, dependency-free state manager for the Company OS."""
from __future__ import annotations
import argparse, json, os, sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # importable both as a module and as a script
from runtime.filelock import exclusive_lock  # noqa: E402

STATE = Path(os.environ.get("MYORG_STATE_DIR", ROOT / "state"))
KINDS = {"goal", "task", "decision"}
STATUSES = {
    "goal": {"proposed", "active", "achieved", "paused", "cancelled"},
    "task": {"planned", "in_progress", "blocked", "awaiting_approval", "done", "cancelled"},
    "decision": {"proposed", "approved", "rejected", "superseded"},
}
TRANSITIONS = {
    "goal": {"proposed": {"active", "paused", "cancelled"}, "active": {"achieved", "paused", "cancelled"}, "paused": {"active", "cancelled"}},
    "task": {"planned": {"in_progress", "blocked", "cancelled"}, "in_progress": {"blocked", "awaiting_approval", "done", "cancelled"}, "blocked": {"in_progress", "cancelled"}, "awaiting_approval": {"in_progress", "done", "cancelled"}},
    "decision": {"proposed": {"approved", "rejected"}, "approved": {"superseded"}},
}

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
def path_for(kind): return STATE / f"{kind}s.jsonl"

@contextmanager
def state_lock():
    with exclusive_lock(STATE/".lock"): yield

def read(kind):
    path = path_for(kind)
    if not path.exists(): return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip(): continue
        try: rows.append(json.loads(line))
        except json.JSONDecodeError as error: raise SystemExit(f"{path}:{number}: invalid JSON: {error}") from error
    return rows

def latest(kind):
    result = {}
    for row in read(kind): result[row["id"]] = row
    return result

def append(kind, row):
    STATE.mkdir(parents=True, exist_ok=True)
    with path_for(kind).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
        handle.flush(); os.fsync(handle.fileno())

def next_id(kind):
    prefix = {"goal": "G", "task": "T", "decision": "D"}[kind]
    numbers = [int(row["id"].split("-")[-1]) for row in read(kind) if row["id"].startswith(prefix + "-")]
    return f"{prefix}-{max(numbers, default=0) + 1:04d}"

def create(args):
    with state_lock():
        if args.kind == "task":
            if not args.goal: raise SystemExit("task requires --goal")
            goal = latest("goal").get(args.goal)
            if not goal or goal["status"] in {"achieved", "cancelled"}: raise SystemExit(f"task goal is missing or closed: {args.goal}")
            if not args.owner: raise SystemExit("task requires --owner")
            if not (ROOT / ".claude" / "agents" / f"{args.owner}.md").is_file(): raise SystemExit(f"unknown task owner: {args.owner}")
        item_id = next_id(args.kind)
        row = {"id": item_id, "kind": args.kind, "status": {"goal":"proposed","task":"planned","decision":"proposed"}[args.kind], "title": args.title.strip(), "created_at": now(), "updated_at": now()}
        for key in ("goal", "owner", "outcome", "evidence", "approval"):
            value = getattr(args, key, None)
            if value: row[key] = value.strip()
        append(args.kind, row)
    print(item_id)

def update(args):
    with state_lock():
        current = latest(args.kind).get(args.id)
        if not current: raise SystemExit(f"unknown {args.kind}: {args.id}")
        if args.status not in TRANSITIONS[args.kind].get(current["status"], set()): raise SystemExit(f"invalid transition: {current['status']} -> {args.status}")
        if args.kind == "goal" and args.status == "achieved":
            open_tasks = [row["id"] for row in latest("task").values() if row.get("goal") == args.id and row["status"] not in {"done", "cancelled"}]
            if open_tasks: raise SystemExit(f"goal has open tasks: {', '.join(open_tasks)}")
        if args.status == "done" and not args.evidence: raise SystemExit("done requires --evidence")
        if args.kind == "decision" and args.status == "approved" and not args.approval: raise SystemExit("approved decision requires --approval")
        row = dict(current); row.update(status=args.status, updated_at=now())
        if args.evidence: row["evidence"] = args.evidence.strip()
        if args.approval: row["approval"] = args.approval.strip()
        append(args.kind, row)
    print(args.id)

def status(_):
    with state_lock():
        for kind in ("goal", "task", "decision"):
            for item_id, row in sorted(latest(kind).items()):
                fields = [item_id, row["status"], row["title"]]
                if row.get("owner"): fields.append(f"owner={row['owner']}")
                if row.get("goal"): fields.append(f"goal={row['goal']}")
                print("\t".join(fields))

def validate(_):
    errors=[]; snapshots={kind:latest(kind) for kind in KINDS}
    for kind in KINDS:
        for row in read(kind):
            missing={"id","kind","status","title","created_at","updated_at"}-row.keys()
            if missing: errors.append(f"{kind}: missing {sorted(missing)}")
            if row.get("kind") != kind or row.get("status") not in STATUSES[kind]: errors.append(f"{row.get('id',kind)}: invalid kind/status")
    for row in snapshots["task"].values():
        if row.get("goal") not in snapshots["goal"]: errors.append(f"{row['id']}: unknown goal {row.get('goal')}")
        if row["status"] == "done" and not row.get("evidence"): errors.append(f"{row['id']}: done without evidence")
        if not (ROOT / ".claude" / "agents" / f"{row.get('owner')}.md").is_file(): errors.append(f"{row['id']}: unknown owner {row.get('owner')}")
    if errors: raise SystemExit("\n".join(errors))
    print("state valid")

def parser():
    result=argparse.ArgumentParser(description=__doc__); commands=result.add_subparsers(dest="command",required=True)
    command=commands.add_parser("create"); command.add_argument("kind",choices=sorted(KINDS)); command.add_argument("title")
    for option in ("goal","owner","outcome","evidence","approval"): command.add_argument(f"--{option}")
    command.set_defaults(func=create)
    command=commands.add_parser("update"); command.add_argument("kind",choices=sorted(KINDS)); command.add_argument("id"); command.add_argument("status"); command.add_argument("--evidence"); command.add_argument("--approval"); command.set_defaults(func=update)
    command=commands.add_parser("status"); command.set_defaults(func=status)
    command=commands.add_parser("validate"); command.set_defaults(func=validate)
    return result

if __name__ == "__main__":
    args=parser().parse_args(); args.func(args)
