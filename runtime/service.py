#!/usr/bin/env python3
"""Organization-scoped application service with human-held approval authority.

`MyOrgService` stays one object -- the API holds exactly one, and every method goes through
the same principal check -- but its methods fall into three domains that touch nothing of
each other's. Each domain lives in its own module and is mixed in below. What all three
share -- the refusals, the role check, the id patterns -- is in `service_core`.
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


from runtime.service_connectors import ConnectorsServiceMixin
from runtime.service_core import (Forbidden, ID_RE, PROJECT_DOCUMENTS, PROJECT_WRITERS, REF_RE,
                                  ROOT, SCOPE_RE, SECRET_REF_RE, SHA256_RE, ServiceError,
                                  _policy, _quietly, _require)
from runtime.service_operator import OperatorServiceMixin
from runtime.service_runs import RunsServiceMixin

__all__ = ["Forbidden", "MyOrgService", "ServiceError"]


class MyOrgService(RunsServiceMixin, ConnectorsServiceMixin,
                   OperatorServiceMixin):
    def __init__(self, store: Store):
        self.store = store
        self.fixture_gateway = FixtureConnectorGateway(store)
        self.live_gateway = LiveConnectorGateway(store)

    def create_run(self, principal: Principal, body: dict, request_id: str) -> tuple[dict, bool]:
        _require(principal, "chief-of-staff", "system-admin")
        allowed = {"id", "workflow_id", "workflow_revision", "goal", "data_class"}
        if set(body) != allowed:
            raise ServiceError("run request must contain exactly the documented fields")
        for key in ("id", "workflow_id"):
            if not ID_RE.fullmatch(str(body[key])):
                raise ServiceError(f"{key} must be a lowercase slug")
        if not SHA256_RE.fullmatch(str(body["workflow_revision"])):
            raise ServiceError("workflow_revision must be SHA-256")
        if body["data_class"] not in {"public", "internal"}:
            raise ServiceError("API run metadata accepts public/internal only; confidential/restricted content must stay in referenced artifacts")
        if not isinstance(body["goal"], str) or not 1 <= len(body["goal"].strip()) <= 500:
            raise ServiceError("goal must be 1..500 characters")
        return self.store.create_run(principal.org_id, body["id"], body["workflow_id"], body["workflow_revision"],
                                     body["goal"].strip(), body["data_class"], principal.actor_id, request_id)

    def _run_state(self, principal: Principal, run_id: str) -> dict:
        """Load one file-log run and prove it belongs to this organization.

        A run's log carries `org_id` only if it was created with one; runs made before the
        field existed, or by `create-run` without `--org`, have none. `projection.project_run`
        already reads a missing value as the default organization, so the read model lists
        those runs -- and every caller here used to compare the raw `None`, which made a run
        the Control Center displays, and offers a Stop button for, answer "unknown run" the
        moment anybody acted on it. One reading of that field, used by every verb.

        An unknown run and another organization's run give the same answer on purpose: the
        error must never confirm that a run exists somewhere else.
        """
        from runtime import company_runtime as core
        if not ID_RE.fullmatch(str(run_id)):
            raise ServiceError("invalid run id")
        try:
            state = core.read_events(run_id)[-1]
        except SystemExit as error:
            raise ServiceError(f"unknown run: {run_id}") from error
        if (state.get("org_id") or core.DEFAULT_ORG) != principal.org_id:
            raise ServiceError(f"unknown run: {run_id}")
        return state

    # --- workflow-step decisions ------------------------------------------------
    # These are the yellow and red gates of a run, which live in the append-only run log.
    # They are a different thing from `request_approval` below, which governs a single
    # connector write. Both end at a named human; only this one can move a run.
