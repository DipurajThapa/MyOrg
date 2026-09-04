#!/usr/bin/env python3
"""SQLite persistence, audit integrity, backup, and restore for MyOrg.

`Store` is one object on purpose -- every caller holds one and every write shares one
connection discipline -- but its methods fall into five domains that touch nothing of each
other's. Each domain lives in its own module and is mixed in below, so this file holds only
what all five share: the connection, the transaction scopes, the migrations, the integrity
check and the backup.

The public names of this module are unchanged: importing `Store`, `Conflict`, `NotFound`,
`StoreError`, `canonical`, `digest`, `utc_now`, `MIGRATIONS` or `restore_backup` from
`runtime.db` works exactly as it did.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from runtime.db_connectors import ConnectorsMixin
from runtime.db_core import (MIGRATIONS, ROOT, Conflict, NotFound, StoreError, canonical,
                             digest, utc_now)
from runtime.db_identity import IdentityMixin
from runtime.db_runs import RunsMixin
from runtime.db_triggers import TriggersMixin
from runtime.db_workspace import WorkspaceMixin

__all__ = ["MIGRATIONS", "ROOT", "Conflict", "NotFound", "Store", "StoreError", "canonical",
           "digest", "restore_backup", "utc_now"]


class Store(IdentityMixin, WorkspaceMixin, RunsMixin, ConnectorsMixin, TriggersMixin):
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def reading(self) -> Iterator[sqlite3.Connection]:
        """Read scope that actually closes. sqlite3's own context manager commits but
        never closes the connection, which leaks the handle and locks the file on Windows."""
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def migrate(self) -> list[int]:
        applied: list[int] = []
        with self.transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            existing = {row["version"]: row for row in connection.execute("SELECT * FROM schema_migrations")}
            for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
                version = int(path.name.split("_", 1)[0])
                sql = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                if version in existing:
                    if existing[version]["checksum"] != checksum:
                        raise StoreError(f"migration checksum changed: {path.name}")
                    continue
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                    (version, path.name, checksum, utc_now()),
                )
                applied.append(version)
        os.chmod(self.path, 0o600)
        return applied

    def purge_transient(self, now: str | None = None, idempotency_days: int = 30) -> dict:
        current = now or utc_now()
        parsed = datetime.fromisoformat(current.replace("Z", "+00:00"))
        cutoff = (parsed - timedelta(days=idempotency_days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.transaction() as connection:
            gateway = connection.execute("DELETE FROM gateway_nonces WHERE expires_at<=?", (current,)).rowcount
            webhook = connection.execute("DELETE FROM webhook_nonces WHERE expires_at<=?", (current,)).rowcount
            revoked = connection.execute("DELETE FROM revoked_tokens WHERE expires_at<=?", (current,)).rowcount
            idempotency = connection.execute("DELETE FROM idempotency_requests WHERE created_at<?", (cutoff,)).rowcount
        return {"gateway_nonces": gateway, "webhook_nonces": webhook, "revoked_tokens": revoked,
                "idempotency_requests": idempotency}

    def verify(self) -> dict:
        with self.reading() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise StoreError(f"database integrity failed: {integrity}")
            migrations = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
            events = connection.execute("SELECT * FROM events ORDER BY org_id,run_id,seq").fetchall()
            operational = connection.execute("SELECT * FROM operational_events ORDER BY org_id,seq").fetchall()
        previous: dict[tuple[str, str], str | None] = {}
        for row in events:
            key = (row["org_id"], row["run_id"])
            expected_previous = previous.get(key)
            if row["previous_hash"] != expected_previous:
                raise StoreError("event chain previous hash mismatch")
            event = {
                "org_id": row["org_id"], "run_id": row["run_id"], "seq": row["seq"],
                "event_type": row["event_type"], "actor_id": row["actor_id"], "request_id": row["request_id"],
                "payload": json.loads(row["payload_json"]), "previous_hash": row["previous_hash"], "created_at": row["created_at"],
            }
            if digest(event) != row["event_hash"]:
                raise StoreError("event hash mismatch")
            previous[key] = row["event_hash"]
        operational_previous: dict[str, str | None] = {}
        for row in operational:
            expected_previous = operational_previous.get(row["org_id"])
            if row["previous_hash"] != expected_previous:
                raise StoreError("operational event chain previous hash mismatch")
            event = {
                "org_id": row["org_id"], "seq": row["seq"], "category": row["category"],
                "action": row["action"], "actor_id": row["actor_id"],
                "resource_type": row["resource_type"], "resource_id": row["resource_id"],
                "request_id": row["request_id"], "trace_id": row["trace_id"],
                "metadata": json.loads(row["metadata_json"]), "previous_hash": row["previous_hash"],
                "created_at": row["created_at"],
            }
            if digest(event) != row["event_hash"]:
                raise StoreError("operational event hash mismatch")
            operational_previous[row["org_id"]] = row["event_hash"]
        return {"integrity": "ok", "migrations": migrations, "events": len(events),
                "operational_events": len(operational)}

    def backup(self, destination: str | Path) -> dict:
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp")
        if temporary.exists():
            temporary.unlink()
        source = self.connect()
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        os.chmod(temporary, 0o600)
        check = Store(temporary).verify()
        os.replace(temporary, destination)
        checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
        manifest = {"version": 1, "database": destination.name, "sha256": checksum, "created_at": utc_now(), "verification": check}
        manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
        manifest_path.write_text(canonical(manifest) + "\n", encoding="utf-8")
        os.chmod(manifest_path, 0o600)
        return manifest

def restore_backup(backup_path: str | Path, target_path: str | Path) -> dict:
    backup = Path(backup_path).resolve()
    target = Path(target_path).resolve()
    manifest_path = backup.with_suffix(backup.suffix + ".manifest.json")
    if not backup.is_file() or not manifest_path.is_file():
        raise StoreError("backup or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("sha256") != hashlib.sha256(backup.read_bytes()).hexdigest():
        raise StoreError("backup checksum mismatch")
    Store(backup).verify()
    target.parent.mkdir(parents=True, exist_ok=True)
    pre_restore = None
    if target.exists():
        pre_restore = target.with_name(f"{target.stem}.pre-restore-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}{target.suffix}")
        Store(target).backup(pre_restore)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".restore", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(backup, temporary)
        Store(temporary).verify()
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"restored": str(target), "source_sha256": manifest["sha256"], "pre_restore": str(pre_restore) if pre_restore else None}
