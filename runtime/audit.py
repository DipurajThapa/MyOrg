#!/usr/bin/env python3
"""The company's accountability record, written by the runtime -- never by an agent.

Before this module the audit log was a behaviour: a skill told an agent to append a line
by hand. An agent that forgets, or is talked out of it, left no trace, and the absence was
itself invisible. Here the line is a side effect of the gate transition, so "who approved
what, when" cannot be skipped without skipping the action itself.

Entries are hash-chained: each one carries the hash of the one before it, so editing any
line breaks every line after it. Lines written before this module existed are sealed too,
by anchoring the first chained entry to a digest of everything that preceded it -- history
is never rewritten to make the chain work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime.filelock import exclusive_lock  # noqa: E402

LOG_ENV = "MYORG_AUDIT_LOG"
FIELDS = ("ts", "actor", "action", "category", "target",
          "approval", "evidence", "outcome", "note")
CATEGORIES = {"green", "yellow", "red"}
APPROVALS = {"not-required", "pending", "granted", "denied"}
OUTCOMES = {"ok", "awaiting-approval", "blocked", "breach-flagged", "refused"}


def log_path() -> Path:
    """Where the record lives. Read at call time, so no caller has to reload the module.

    The log follows the runs it describes: point `MYORG_RUNS_DIR` somewhere else -- as
    every test does -- and the audit log goes with it, so a test run can never append to
    the company's real record.
    """
    override = os.environ.get(LOG_ENV)
    if override: return Path(override)
    runs = os.environ.get("MYORG_RUNS_DIR")
    # The leading underscore matters: the runtime treats every other *.jsonl in the runs
    # directory as a run, and would try to replay this one. `_outbox.jsonl` is named the
    # same way for the same reason.
    if runs: return Path(runs) / "_audit-log.jsonl"
    return ROOT / "logs" / "audit-log.jsonl"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def entry_digest(entry: dict) -> str:
    payload = {k: v for k, v in entry.items() if k != "entry_hash"}
    return digest(payload)


def resolve_evidence(value: str) -> str:
    """Evidence is a path, repo-relative where it can be. It must actually be there."""
    if (ROOT / value).exists() or Path(value).exists():
        return value
    raise SystemExit(f"audit evidence path does not exist: {value}")


def read_lines() -> list[str]:
    path = log_path()
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def split_chain(lines: list[str]) -> tuple[list[str], list[dict]]:
    """Lines predating the chain, then the chained entries after them."""
    legacy: list[str] = []
    chained: list[dict] = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            record = {}
        if chained or "entry_hash" in record:
            chained.append(record)
        else:
            legacy.append(line)
    return legacy, chained


def anchor(legacy: list[str]) -> str | None:
    """One digest sealing every line written before the chain began."""
    if not legacy:
        return None
    return hashlib.sha256("\n".join(legacy).encode()).hexdigest()


def validate(entry: dict) -> None:
    missing = [field for field in FIELDS if not str(entry.get(field, "")).strip()
               and field != "note"]
    if missing:
        raise SystemExit(f"audit entry is missing: {', '.join(missing)}")
    if entry["category"] not in CATEGORIES:
        raise SystemExit(f"audit category must be one of {sorted(CATEGORIES)}")
    if entry["approval"] not in APPROVALS:
        raise SystemExit(f"audit approval must be one of {sorted(APPROVALS)}")
    if entry["outcome"] not in OUTCOMES:
        raise SystemExit(f"audit outcome must be one of {sorted(OUTCOMES)}")


def append(actor: str, action: str, category: str, target: str, approval: str,
           evidence: str, outcome: str, note: str = "") -> dict:
    """Add one line. Raises rather than returning quietly, so callers can fail closed."""
    entry = {"ts": now(), "actor": actor, "action": action, "category": category,
             "target": target, "approval": approval,
             "evidence": resolve_evidence(evidence), "outcome": outcome, "note": note}
    validate(entry)
    path = log_path()
    if path.is_dir():
        raise SystemExit(f"audit log path is a directory: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(path.with_suffix(path.suffix + ".lock")):
        legacy, chained = split_chain(read_lines())
        entry["prev_hash"] = chained[-1].get("entry_hash") if chained else anchor(legacy)
        entry["entry_hash"] = entry_digest(entry)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return entry


def verify() -> list[str]:
    """Every problem found in the chain. An empty list means the record is intact."""
    legacy, chained = split_chain(read_lines())
    problems: list[str] = []
    previous = anchor(legacy)
    for position, record in enumerate(chained, start=len(legacy) + 1):
        if "entry_hash" not in record:
            problems.append(f"line {position}: unchained line after the chain began")
            continue
        if record.get("prev_hash") != previous:
            problems.append(f"line {position}: previous hash does not match")
        if record["entry_hash"] != entry_digest(record):
            problems.append(f"line {position}: entry hash does not match its contents")
        previous = record["entry_hash"]
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "tail"))
    parser.add_argument("--lines", type=int, default=10)
    args = parser.parse_args(argv)
    if args.command == "verify":
        problems = verify()
        print("audit log intact" if not problems else "\n".join(problems))
        return 0 if not problems else 1
    for line in read_lines()[-args.lines:]:
        record = json.loads(line)
        print(f"{record['ts']}\t{record['actor']}\t{record['action']}\t"
              f"{record['category']}\t{record['approval']}\t{record['outcome']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
