#!/usr/bin/env python3
"""What the company knows, carried from one run to the next.

Without this every run starts from zero: the department that was corrected yesterday
repeats the mistake today. Entries are append-only and hash-chained like the run log, and
an agent may only *propose* -- nothing reaches another agent's prompt until a human
approves it, because a poisoned shared memory silently bends every later decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime.filelock import exclusive_lock  # noqa: E402

STORE = Path(os.environ.get("MYORG_MEMORY_DIR", ROOT / "memory"))
DEFAULT_ORG = os.environ.get("MYORG_ORG_ID", "default")
KINDS = {"fact", "lesson"}
STATUSES = {"proposed", "approved", "rejected", "retired"}
LIVE = "approved"
MAX_BODY_CHARS = 600
MAX_RECALL = 5
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
WORD = re.compile(r"[a-z0-9]{4,}")
STOPWORDS = {"this", "that", "with", "from", "have", "into", "than", "then", "they",
             "what", "when", "which", "will", "would", "there", "their", "about",
             "step", "goal", "work", "run", "company", "output", "produce"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def store_path(org_id: str) -> Path:
    if not ID_RE.fullmatch(org_id):
        raise SystemExit(f"invalid org id: {org_id}")
    return STORE / f"{org_id}.memory.jsonl"


@dataclass(frozen=True)
class Entry:
    """One thing the company believes, and who let it believe that."""
    id: str
    org_id: str
    kind: str
    subject: str
    body: str
    author: str
    status: str
    source_run: str = ""
    source_step: str = ""
    decided_by: str = ""
    ts: str = ""

    @property
    def live(self) -> bool:
        return self.status == LIVE

    def as_prompt_line(self) -> str:
        return f"- [{self.kind}] {self.subject}: {self.body}"


def digest(record: dict) -> str:
    payload = {key: value for key, value in record.items() if key != "hash"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_records(org_id: str) -> list[dict]:
    """Every record in order, refusing the file outright if the chain is broken."""
    path = store_path(org_id)
    if not path.is_file():
        return []
    records, previous = [], None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("prev_hash") != previous:
            raise SystemExit(f"{path}:{number}: memory chain is broken")
        if record.get("hash") != digest(record):
            raise SystemExit(f"{path}:{number}: memory record was altered")
        previous = record["hash"]
        records.append(record)
    return records


def append_record(org_id: str, record: dict) -> dict:
    path = store_path(org_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(path.with_suffix(".lock")):
        existing = read_records(org_id)
        record["prev_hash"] = existing[-1]["hash"] if existing else None
        record["hash"] = digest(record)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return record


def current(org_id: str = DEFAULT_ORG) -> list[Entry]:
    """The latest state of every entry -- later records supersede earlier ones."""
    latest: dict[str, dict] = {}
    for record in read_records(org_id):
        latest[record["id"]] = {**latest.get(record["id"], {}), **record}
    return [Entry(id=r["id"], org_id=org_id, kind=r["kind"], subject=r["subject"],
                  body=r["body"], author=r["author"], status=r["status"],
                  source_run=r.get("source_run", ""), source_step=r.get("source_step", ""),
                  decided_by=r.get("decided_by", ""), ts=r.get("ts", ""))
            for r in latest.values()]


def propose(subject: str, body: str, author: str, kind: str = "lesson",
            org_id: str = DEFAULT_ORG, source_run: str = "",
            source_step: str = "") -> Entry | None:
    """An agent offering something it learned. Returns None if it is already known."""
    if kind not in KINDS:
        raise SystemExit(f"unknown memory kind: {kind}")
    subject, body = " ".join(subject.split()), " ".join(body.split())[:MAX_BODY_CHARS]
    if not subject or not body:
        raise SystemExit("a memory needs both a subject and a body")
    existing = current(org_id)
    if any(e.subject.lower() == subject.lower() and e.status in ("proposed", LIVE)
           for e in existing):
        return None
    identifier = f"mem-{hashlib.sha256(subject.lower().encode()).hexdigest()[:10]}"
    append_record(org_id, {
        "id": identifier, "ts": now(), "kind": kind, "subject": subject, "body": body,
        "author": author, "status": "proposed", "source_run": source_run,
        "source_step": source_step})
    return next(e for e in current(org_id) if e.id == identifier)


def decide(entry_id: str, status: str, decided_by: str,
           org_id: str = DEFAULT_ORG) -> Entry:
    """A human accepting, refusing, or retiring something the company believes."""
    if status not in STATUSES:
        raise SystemExit(f"unknown status: {status}")
    if not decided_by.strip():
        raise SystemExit("memory decisions are attributable -- who decided?")
    existing = {entry.id: entry for entry in current(org_id)}
    if entry_id not in existing:
        raise SystemExit(f"unknown memory entry: {entry_id}")
    entry = existing[entry_id]
    append_record(org_id, {
        "id": entry_id, "ts": now(), "kind": entry.kind, "subject": entry.subject,
        "body": entry.body, "author": entry.author, "status": status,
        "source_run": entry.source_run, "source_step": entry.source_step,
        "decided_by": decided_by.strip()})
    return next(e for e in current(org_id) if e.id == entry_id)


def keywords(text: str) -> set[str]:
    return {word for word in WORD.findall(text.lower()) if word not in STOPWORDS}


def recall(text: str, org_id: str = DEFAULT_ORG, limit: int = MAX_RECALL) -> list[Entry]:
    """Approved entries that overlap what the agent is about to do, best first.

    Deliberately plain keyword overlap: no dependencies, and an operator can always see
    why a memory surfaced.
    """
    wanted = keywords(text)
    if not wanted:
        return []
    scored = []
    for entry in current(org_id):
        if not entry.live:
            continue
        overlap = len(wanted & keywords(f"{entry.subject} {entry.body}"))
        if overlap:
            scored.append((overlap, entry.ts, entry))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [entry for _, _, entry in scored[:limit]]


def proposals(org_id: str = DEFAULT_ORG) -> list[Entry]:
    return sorted((e for e in current(org_id) if e.status == "proposed"),
                  key=lambda e: e.ts)


def render(entries: list[Entry]) -> str:
    if not entries:
        return "Nothing here yet."
    return "\n".join(f"{e.id}  {e.status:<9} {e.kind:<7} {e.subject}\n"
                     f"{'':>12}{e.body}" for e in entries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", default=DEFAULT_ORG)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list")
    commands.add_parser("proposals")

    recalling = commands.add_parser("recall")
    recalling.add_argument("text")

    proposing = commands.add_parser("propose")
    proposing.add_argument("subject")
    proposing.add_argument("body")
    proposing.add_argument("--author", required=True)
    proposing.add_argument("--kind", choices=sorted(KINDS), default="lesson")

    for name in ("approve", "reject", "retire"):
        command = commands.add_parser(name)
        command.add_argument("entry_id")
        command.add_argument("--by", required=True)

    args = parser.parse_args(argv)
    if args.command == "list":
        print(render(current(args.org)))
    elif args.command == "proposals":
        print(render(proposals(args.org)))
    elif args.command == "recall":
        print(render(recall(args.text, args.org)))
    elif args.command == "propose":
        entry = propose(args.subject, args.body, args.author, args.kind, args.org)
        print(entry.id if entry else "already known")
    else:
        status = {"approve": LIVE, "reject": "rejected", "retire": "retired"}[args.command]
        print(decide(args.entry_id, status, args.by, args.org).status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
