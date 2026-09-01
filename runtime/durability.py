#!/usr/bin/env python3
"""Checking that the company's own record is intact, and being able to get it back.

The SQLite side has had backup, restore and verification since the beginning. The event
log -- which is where the work actually lives -- had none of it. Every run, its evidence,
the shared memory and the outbox could be silently corrupted or lost with nothing to say
so. This closes that.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sys
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402

MANIFEST_NAME = "backup.manifest.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class Check:
    """What survived inspection, and what did not."""
    runs_ok: list[str] = field(default_factory=list)
    runs_broken: dict[str, str] = field(default_factory=dict)
    evidence_missing: list[str] = field(default_factory=list)
    evidence_altered: list[str] = field(default_factory=list)
    memory_broken: dict[str, str] = field(default_factory=dict)

    @property
    def sound(self) -> bool:
        return not (self.runs_broken or self.evidence_missing
                    or self.evidence_altered or self.memory_broken)

    def summary(self) -> str:
        if self.sound:
            return (f"{len(self.runs_ok)} run(s) intact; every evidence file matches "
                    "its recorded hash.")
        parts = []
        if self.runs_broken:
            parts.append(f"{len(self.runs_broken)} run(s) with a broken chain")
        if self.evidence_missing:
            parts.append(f"{len(self.evidence_missing)} missing evidence file(s)")
        if self.evidence_altered:
            parts.append(f"{len(self.evidence_altered)} altered evidence file(s)")
        if self.memory_broken:
            parts.append(f"{len(self.memory_broken)} damaged memory store(s)")
        return "DAMAGE: " + "; ".join(parts)


def check_evidence(state: dict, report: Check) -> None:
    """Evidence is referenced by hash; a file that no longer matches is not evidence."""
    for step_id, step in state["steps"].items():
        recorded = step.get("evidence")
        if not recorded:
            continue
        where = f"{state['run_id']}/{step_id}"
        path = ROOT / recorded
        if not path.is_file():
            report.evidence_missing.append(where)
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != step.get("evidence_sha256"):
            report.evidence_altered.append(where)


def check_memory(report: Check) -> None:
    from runtime.memory import STORE, current
    for path in sorted(STORE.glob("*.memory.jsonl")):
        org_id = path.name.split(".", 1)[0]
        try:
            current(org_id)
        except SystemExit as error:
            report.memory_broken[org_id] = str(error)
        except Exception as error:  # noqa: BLE001 - a damaged store must be reported
            report.memory_broken[org_id] = repr(error)



def verify() -> Check:
    """Read everything the way the runtime reads it, and report what fails."""
    report = Check()
    for path in core.run_files():
        try:
            state = core.read_events(path.stem)[-1]
        except SystemExit as error:
            report.runs_broken[path.stem] = str(error)
            continue
        report.runs_ok.append(path.stem)
        check_evidence(state, report)
    check_memory(report)
    return report


def sources() -> dict[str, Path]:
    """Everything that would have to come back for the company to carry on.

    Keyed by a logical name, not a repo path: these directories are configurable, so a
    backup must not assume where they lived when it was taken.
    """
    from runtime.memory import STORE
    found = {"runs": core.RUNS}
    if STORE.resolve() != core.RUNS.resolve():
        found["memory"] = STORE
    return {name: path for name, path in found.items() if path.is_dir()}


def backup(destination: Path) -> dict:
    """One archive holding the log, its evidence and the shared memory."""
    report = verify()
    destination.parent.mkdir(parents=True, exist_ok=True)
    held = sources()
    manifest = {"created_at": now(), "sound_at_backup": report.sound,
                "summary": report.summary(), "runs": sorted(report.runs_ok),
                "directories": sorted(held),
                "taken_from": {name: str(path) for name, path in held.items()}}
    with tarfile.open(destination, "w:gz") as archive:
        for name, path in held.items():
            archive.add(path, arcname=name)
        payload = json.dumps(manifest, indent=2).encode("utf-8")
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    manifest["sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
    manifest["archive"] = str(destination)
    return manifest


def read_manifest(archive_path: Path) -> dict:
    with tarfile.open(archive_path, "r:gz") as archive:
        member = archive.extractfile(MANIFEST_NAME)
        if member is None:
            raise SystemExit(f"{archive_path} has no {MANIFEST_NAME}")
        return json.loads(member.read())


def restore(archive_path: Path, target: Path, force: bool = False) -> dict:
    """Unpack a backup. Refuses to overwrite live state unless told to."""
    if not archive_path.is_file():
        raise SystemExit(f"no such backup: {archive_path}")
    manifest = read_manifest(archive_path)
    for relative in manifest["directories"]:
        existing = target / relative
        if existing.exists() and any(existing.iterdir()) and not force:
            raise SystemExit(
                f"{relative} already holds data; pass --force to overwrite it")
    for relative in manifest["directories"]:
        shutil.rmtree(target / relative, ignore_errors=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = [m for m in archive.getmembers() if m.name != MANIFEST_NAME]
        for member in members:
            if member.name.startswith(("/", "..")) or ".." in Path(member.name).parts:
                raise SystemExit(f"refusing unsafe path in archive: {member.name}")
        archive.extractall(target, members=members)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify")

    saving = commands.add_parser("backup")
    saving.add_argument("destination", type=Path)

    loading = commands.add_parser("restore")
    loading.add_argument("archive", type=Path)
    loading.add_argument("--target", type=Path, default=ROOT)
    loading.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "verify":
        report = verify()
        print(report.summary())
        for run_id, error in sorted(report.runs_broken.items()):
            print(f"  {run_id}: {error}")
        for where in sorted(report.evidence_missing + report.evidence_altered):
            print(f"  evidence problem: {where}")
        return 0 if report.sound else 1
    if args.command == "backup":
        manifest = backup(args.destination)
        print(f"{manifest['archive']}  sha256={manifest['sha256'][:16]}…  "
              f"{len(manifest['runs'])} run(s); {manifest['summary']}")
        return 0
    manifest = restore(args.archive, args.target, args.force)
    print(f"restored {len(manifest['runs'])} run(s) from {args.archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
