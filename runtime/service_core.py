#!/usr/bin/env python3
"""The refusals and the small helpers every part of the service shares.

Split out so a domain module can have them without importing `service`, which imports the
domain modules. Nothing here reaches the database.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import secrets
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime import triggers
from runtime.auth import Principal
from runtime.connectors import FixtureConnectorGateway, action_digest
from runtime.db import Store
from runtime.live_gateway import LiveConnectorGateway

ROOT = Path(__file__).resolve().parents[1]
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SECRET_REF_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
SCOPE_RE = re.compile(r"^[A-Za-z0-9:./_-]{1,160}$")
PROJECT_DOCUMENTS = {"problem_statement", "charter", "sop", "control_plan", "uat", "release_checklist"}
PROJECT_WRITERS = ("maker", "chief-of-staff", "system-admin")



class ServiceError(RuntimeError):
    pass


class Forbidden(ServiceError):
    pass


def _require(principal: Principal, *roles: str) -> None:
    if not principal.has_role(*roles):
        raise Forbidden("role is not authorized for this operation")


def _quietly(command, arguments) -> None:
    """Runtime commands print their new status; the API answers in JSON instead."""
    with redirect_stdout(io.StringIO()):
        command(arguments)


def _policy() -> dict[str, str]:
    data = json.loads((ROOT / "runtime" / "policy.json").read_text(encoding="utf-8"))
    return data["actions"]


