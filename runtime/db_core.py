#!/usr/bin/env python3
"""The primitives every part of the store shares: its errors, its clock, its hashes.

Split out so the domain mixins can import them without importing `db` itself, which
imports the mixins. Nothing here touches the database.
"""
from __future__ import annotations

import hashlib
import json

from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "runtime" / "migrations"


class StoreError(RuntimeError):
    pass


class Conflict(StoreError):
    pass


class NotFound(StoreError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def digest(value: object) -> str:
    raw = value if isinstance(value, bytes) else canonical(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
