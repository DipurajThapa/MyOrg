#!/usr/bin/env python3
"""Organization-scoped application service with human-held approval authority."""
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


class MyOrgService:
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

    # --- workflow-step decisions ------------------------------------------------
    # These are the yellow and red gates of a run, which live in the append-only run log.
    # They are a different thing from `request_approval` below, which governs a single
    # connector write. Both end at a named human; only this one can move a run.

    def runs(self, principal: Principal) -> list[dict]:
        """Every run in this organization, newest change first, with what a person can do
        about it. Read-only; the same audience that may see the decision queue."""
        from runtime import company_runtime as core
        rows = self.store.runs(principal.org_id)
        for row in rows:
            row["can_cancel"] = row.get("runtime_status") not in core.TERMINAL_RUN
        return rows

    def pending_decisions(self, principal: Principal) -> list[dict]:
        """Everything in this organization waiting on a person, worst first."""
        from runtime import approvals
        return [{"run_id": d.run_id, "step": d.step_id, "status": d.status,
                 "owner": d.owner, "action": d.action, "risk": d.risk, "goal": d.goal,
                 "workflow_id": d.workflow_id, "reason": d.reason, "impact": d.impact,
                 "actionable": d.actionable, "unblocks": d.unblocks, "depth": d.depth,
                 "waiting_since": d.waiting_since, "held_reason": d.held_reason,
                 "context": [{"step": step, "excerpt": text} for step, text in d.context],
                 "brief": d.brief.as_text() if d.brief else ""}
                for d in approvals.pending(org_id=principal.org_id)]

    def decide_step(self, principal: Principal, run_id: str, step_id: str,
                    body: dict, request_id: str) -> dict:
        """Approve or reject one parked step, as a named human, with a stated reason."""
        _require(principal, "decision-owner")
        if principal.actor_type != "human":
            raise Forbidden("decisions require a registered human identity")
        if set(body) != {"decision", "reason"} or body["decision"] not in {"approve", "reject"}:
            raise ServiceError("decision must be approve/reject with a reason")
        reason = str(body["reason"]).strip()
        if not 1 <= len(reason) <= 200 or not reason.isprintable():
            raise ServiceError("reason must be 1..200 printable characters on one line")
        if not ID_RE.fullmatch(str(run_id)) or not ID_RE.fullmatch(str(step_id)):
            raise ServiceError("invalid run or step id")

        from runtime import company_runtime as core
        try:
            state = core.read_events(run_id)[-1]
        except SystemExit as error:
            raise ServiceError(f"unknown run: {run_id}") from error
        if state.get("org_id") != principal.org_id:
            # Same answer as a run that does not exist: never confirm another org's runs.
            raise ServiceError(f"unknown run: {run_id}")
        step = state["steps"].get(step_id)
        if not step:
            raise ServiceError(f"unknown step: {step_id}")
        if step["status"] != "awaiting_approval":
            raise ServiceError(
                f"{step_id} is {step['status']}, which is not a decision anyone can take")

        who = principal.display_name or principal.actor_id
        command = core.approve if body["decision"] == "approve" else core.reject
        arguments = argparse.Namespace(run_id=run_id, step=step_id, approver=who,
                                       approval_ref=reason, request_id=request_id)
        try:
            _quietly(command, arguments)
        except SystemExit as error:
            raise ServiceError(str(error)) from error
        self.store.record_step_decision(principal.org_id, principal.actor_id, run_id,
                                        step_id, body["decision"], request_id)
        return {"run_id": run_id, "step": step_id, "decision": body["decision"],
                "status": core.read_events(run_id)[-1]["steps"][step_id]["status"]}

    def cancel_run(self, principal: Principal, run_id: str, body: dict, request_id: str) -> dict:
        """Stop a run, as a named human, with a stated reason (B-02). Same authority and
        same org scoping as a step decision: an agent cannot stop its own run to escape a
        gate, and another org's run answers exactly like a run that does not exist."""
        _require(principal, "decision-owner")
        if principal.actor_type != "human":
            raise Forbidden("stopping a run requires a registered human identity")
        if set(body) != {"reason"}:
            raise ServiceError("a cancel takes exactly one field: reason")
        reason = str(body["reason"]).strip()
        if not 1 <= len(reason) <= 200 or not reason.isprintable():
            raise ServiceError("reason must be 1..200 printable characters on one line")
        if not ID_RE.fullmatch(str(run_id)):
            raise ServiceError("invalid run id")

        from runtime import company_runtime as core
        try:
            state = core.read_events(run_id)[-1]
        except SystemExit as error:
            raise ServiceError(f"unknown run: {run_id}") from error
        if state.get("org_id") != principal.org_id:
            raise ServiceError(f"unknown run: {run_id}")
        who = principal.display_name or principal.actor_id
        try:
            _quietly(core.cancel_run, argparse.Namespace(
                run_id=run_id, approver=who, reason=reason, request_id=request_id))
        except SystemExit as error:
            raise ServiceError(str(error)) from error
        return {"run_id": run_id, "status": core.read_events(run_id)[-1]["run_status"]}

    def request_approval(self, principal: Principal, body: dict, request_id: str) -> dict:
        _require(principal, "maker", "chief-of-staff", "system-admin")
        required = {"run_id", "connector_id", "action", "target_ref", "payload_ref", "payload_sha256"}
        if set(body) != required:
            raise ServiceError("approval request must contain exactly the documented fields")
        if _policy().get(body["action"]) != "yellow":
            raise ServiceError("only yellow actions can enter the approval workflow")
        for key in ("run_id", "connector_id"):
            if not ID_RE.fullmatch(str(body[key])):
                raise ServiceError(f"invalid {key}")
        if not REF_RE.fullmatch(str(body["target_ref"])) or not REF_RE.fullmatch(str(body["payload_ref"])):
            raise ServiceError("target and payload must be bounded references, not raw content")
        connector = self.store.connector(principal.org_id, body["connector_id"])
        if not connector["enabled"] or connector["mode"] != "propose_write" or body["action"] not in connector["allowed_actions"]:
            raise ServiceError("connector is not enabled for the requested proposed write")
        exact_hash = action_digest(body["connector_id"], body["action"], body["target_ref"], body["payload_ref"], body["payload_sha256"])
        approval_id = f"approval-{secrets.token_hex(8)}"
        expires = datetime.now(timezone.utc) + timedelta(minutes=15)
        return self.store.create_approval(
            principal.org_id, approval_id, body["run_id"], body["action"], exact_hash,
            body["target_ref"], body["payload_ref"], body["payload_sha256"], principal.actor_id,
            expires.isoformat(timespec="seconds").replace("+00:00", "Z"), request_id,
        )

    def decide_approval(self, principal: Principal, approval_id: str, body: dict, request_id: str) -> dict:
        _require(principal, "decision-owner")
        if principal.actor_type != "human":
            raise Forbidden("approval decisions require a registered human identity")
        if set(body) != {"decision", "action_hash"} or body["decision"] not in {"approve", "reject"}:
            raise ServiceError("decision must be approve/reject with the exact action_hash")
        if not SHA256_RE.fullmatch(str(body["action_hash"])):
            raise ServiceError("action_hash must be SHA-256")
        return self.store.decide_approval(principal.org_id, approval_id, principal.actor_id,
                                          body["action_hash"], body["decision"], request_id)

    def execute_fixture(self, principal: Principal, body: dict, idempotency_key: str) -> tuple[dict, bool]:
        _require(principal, "connector-gateway")
        if principal.actor_type not in {"agent", "service"}:
            raise Forbidden("connector execution requires a service identity")
        required = {"connector_id", "action", "target_ref", "payload_ref", "payload_sha256", "approval_id"}
        if set(body) != required:
            raise ServiceError("connector request must contain exactly the documented fields")
        return self.fixture_gateway.execute(
            principal.org_id, body["connector_id"], body["action"], body["target_ref"], body["payload_ref"],
            body["payload_sha256"], body["approval_id"], principal.actor_id, idempotency_key,
        )

    def execute_live(self, principal: Principal, body: dict, idempotency_key: str) -> tuple[dict, bool]:
        """A real outward write. Same admission as the fixture path, plus a settlement that
        can say "we do not know" -- which is the only honest answer to a timeout."""
        _require(principal, "connector-gateway")
        if principal.actor_type not in {"agent", "service"}:
            raise Forbidden("connector execution requires a service identity")
        required = {"connector_id", "action", "target_ref", "payload_ref", "payload_sha256",
                    "approval_id", "payload"}
        if set(body) != required:
            raise ServiceError("connector request must contain exactly the documented fields")
        if not isinstance(body["payload"], dict):
            raise ServiceError("connector payload must be a JSON object")
        return self.live_gateway.execute(
            principal.org_id, body["connector_id"], body["action"], body["target_ref"],
            body["payload_ref"], body["payload_sha256"], body["approval_id"], principal.actor_id,
            idempotency_key, body["payload"])

    def in_flight_receipts(self, principal: Principal) -> list[dict]:
        """Calls that left and never resolved. Nothing retries these; a person decides."""
        _require(principal, "system-admin", "auditor", "chief-of-staff")
        return self.store.in_flight_connector_receipts(principal.org_id)

    # --- triggers: what may start work when nobody is at the keyboard --------------------

    def register_webhook_trigger(self, principal: Principal, body: dict,
                                 request_id: str, trace_id: str) -> dict:
        _require(principal, "system-admin")
        if principal.actor_type != "human":
            raise Forbidden("registering a trigger requires a registered human identity")
        if set(body) != {"connector_id", "event_type", "goal", "enabled"} \
                or type(body["enabled"]) is not bool:
            raise ServiceError("trigger requires connector_id, event_type, goal and enabled")
        if not ID_RE.fullmatch(str(body["connector_id"])) \
                or not triggers.EVENT_TYPE_RE.fullmatch(str(body["event_type"])):
            raise ServiceError("connector_id and event_type must be slugs")
        goal = str(body["goal"]).strip()
        if not 1 <= len(goal) <= 500:
            raise ServiceError("goal must be 1..500 characters")
        return self.store.register_webhook_trigger(
            principal.org_id, body["connector_id"], body["event_type"], goal,
            body["enabled"], principal.actor_id, request_id, trace_id)

    def create_schedule(self, principal: Principal, body: dict, request_id: str, trace_id: str) -> dict:
        _require(principal, "system-admin")
        if principal.actor_type != "human":
            raise Forbidden("creating a schedule requires a registered human identity")
        if set(body) != {"id", "kind", "interval_seconds", "daily_at", "goal"}:
            raise ServiceError("schedule requires id, kind, interval_seconds, daily_at and goal")
        if not ID_RE.fullmatch(str(body["id"])) or body["kind"] not in {"interval", "daily"}:
            raise ServiceError("schedule id must be a slug and kind must be interval or daily")
        goal = str(body["goal"]).strip()
        if not 1 <= len(goal) <= 500:
            raise ServiceError("goal must be 1..500 characters")
        interval, daily = body["interval_seconds"], body["daily_at"]
        if body["kind"] == "interval" and (type(interval) is not int or daily is not None):
            raise ServiceError("interval schedules need interval_seconds and a null daily_at")
        if body["kind"] == "daily" and (interval is not None or not isinstance(daily, str)):
            raise ServiceError("daily schedules need daily_at and a null interval_seconds")
        try:
            following = triggers.next_fire(body["kind"], triggers.utc_now(), interval, daily)
        except triggers.TriggerError as error:
            raise ServiceError(str(error)) from error
        return self.store.create_schedule(
            principal.org_id, body["id"], body["kind"], goal, triggers.stamp(following),
            principal.actor_id, request_id, trace_id, interval, daily)

    def schedules(self, principal: Principal) -> list[dict]:
        _require(principal, "system-admin", "auditor", "chief-of-staff")
        return self.store.schedules(principal.org_id)

    def set_schedule_enabled(self, principal: Principal, schedule_id: str, body: dict,
                             request_id: str, trace_id: str) -> dict:
        _require(principal, "system-admin")
        if principal.actor_type != "human":
            raise Forbidden("pausing or resuming a schedule requires a registered human identity")
        if set(body) != {"enabled"} or type(body["enabled"]) is not bool:
            raise ServiceError("schedule status requires exactly one boolean enabled field")
        return self.store.set_schedule_enabled(principal.org_id, schedule_id, body["enabled"],
                                               principal.actor_id, request_id, trace_id)

    def connector_inventory(self, principal: Principal) -> list[dict]:
        _require(principal, "system-admin", "auditor", "chief-of-staff")
        result = []
        for connector in self.store.connectors(principal.org_id):
            connector.pop("secret_ref", None)
            authorization = self.store.connector_authorization(principal.org_id, connector["id"])
            connector["authorization"] = ({"status": authorization["status"],
                                             "scopes": authorization["scopes"],
                                             "expires_at": authorization["expires_at"]}
                                            if authorization else None)
            result.append(connector)
        return result

    def set_connector_enabled(self, principal: Principal, connector_id: str, body: dict,
                              request_id: str, trace_id: str) -> dict:
        _require(principal, "system-admin")
        if principal.actor_type != "human":
            raise Forbidden("connector enablement requires a registered human identity")
        if set(body) != {"enabled"} or type(body["enabled"]) is not bool:
            raise ServiceError("connector status requires exactly one boolean enabled field")
        result = self.store.set_connector_enabled(principal.org_id, connector_id, body["enabled"],
                                                  principal.actor_id, request_id, trace_id)
        result.pop("secret_ref", None)
        return result

    def authorize_connector(self, principal: Principal, connector_id: str, body: dict,
                            request_id: str, trace_id: str) -> dict:
        _require(principal, "system-admin")
        if principal.actor_type != "human":
            raise Forbidden("connector authorization requires a registered human identity")
        required = {"provider_account_ref", "scopes", "token_secret_ref", "refresh_secret_ref", "expires_at"}
        if set(body) != required or not REF_RE.fullmatch(str(body["provider_account_ref"])):
            raise ServiceError("invalid connector authorization record")
        scopes = body["scopes"]
        if not isinstance(scopes, list) or not scopes or len(scopes) > 32 \
                or any(not isinstance(scope, str) or not SCOPE_RE.fullmatch(scope) for scope in scopes):
            raise ServiceError("connector scopes must be a bounded non-empty list")
        if not SECRET_REF_RE.fullmatch(str(body["token_secret_ref"])):
            raise ServiceError("token_secret_ref must be an environment-variable name")
        refresh = body["refresh_secret_ref"]
        if refresh is not None and not SECRET_REF_RE.fullmatch(str(refresh)):
            raise ServiceError("refresh_secret_ref must be null or an environment-variable name")
        try:
            expires = datetime.fromisoformat(str(body["expires_at"]).replace("Z", "+00:00"))
        except ValueError as error:
            raise ServiceError("expires_at must be RFC 3339") from error
        if expires.tzinfo is None or expires <= datetime.now(timezone.utc) or expires > datetime.now(timezone.utc) + timedelta(days=366):
            raise ServiceError("connector authorization expiry must be within the next 366 days")
        return self.store.authorize_connector(
            principal.org_id, connector_id, body["provider_account_ref"], sorted(set(scopes)),
            body["token_secret_ref"], refresh,
            expires.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            principal.actor_id, request_id, trace_id)

    def revoke_connector_authorization(self, principal: Principal, connector_id: str,
                                       request_id: str, trace_id: str) -> dict:
        _require(principal, "system-admin")
        if principal.actor_type != "human":
            raise Forbidden("connector authorization revocation requires a registered human identity")
        return self.store.revoke_connector_authorization(principal.org_id, connector_id, principal.actor_id,
                                                         request_id, trace_id)

    def reconcile_connector_receipt(self, principal: Principal, receipt_id: str, body: dict,
                                    request_id: str, trace_id: str) -> dict:
        _require(principal, "system-admin", "auditor")
        if principal.actor_type != "human":
            raise Forbidden("connector reconciliation requires a registered human identity")
        if set(body) != {"provider_status", "details_sha256"} \
                or body["provider_status"] not in {"confirmed", "rejected", "pending"} \
                or not SHA256_RE.fullmatch(str(body["details_sha256"])):
            raise ServiceError("reconciliation requires provider_status and a details SHA-256")
        return self.store.reconcile_connector_receipt(principal.org_id, receipt_id, body["provider_status"],
                                                      body["details_sha256"], principal.actor_id,
                                                      request_id, trace_id)

    def ui_state(self, principal: Principal) -> dict:
        return self.store.ui_state(principal.org_id, principal.actor_id)

    def save_ui_state(self, principal: Principal, body: dict, request_id: str, trace_id: str) -> dict:
        required = {"schema_version", "active_view", "time_range", "filters", "sort", "scroll_position",
                    "current_project_id", "revision"}
        if set(body) != required or body["schema_version"] != 1:
            raise ServiceError("UI state must match schema version 1 exactly")
        if body["active_view"] not in {"overview", "intake", "queue", "flow", "autonomy"}:
            raise ServiceError("invalid active view")
        if body["time_range"] not in {"7d", "30d", "90d", "all"}:
            raise ServiceError("invalid time range")
        if body["filters"] not in ({"queue": "all", "flow": "current"},
                                   {"queue": "all", "flow": "future"},
                                   {"queue": "attention", "flow": "current"},
                                   {"queue": "attention", "flow": "future"}):
            raise ServiceError("invalid UI filters")
        if body["sort"] not in ({"queue": "updated_desc"}, {"queue": "updated_asc"}):
            raise ServiceError("invalid UI sort")
        if isinstance(body["scroll_position"], bool) or not isinstance(body["scroll_position"], int) \
                or not 0 <= body["scroll_position"] <= 10_000_000:
            raise ServiceError("scroll position must be an integer in range")
        project_id = body["current_project_id"]
        if project_id is not None and not ID_RE.fullmatch(str(project_id)):
            raise ServiceError("invalid current project id")
        revision = body["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ServiceError("UI state revision must be a non-negative integer")
        state = {key: body[key] for key in required - {"revision"}}
        return self.store.save_ui_state(principal.org_id, principal.actor_id, state, revision, request_id, trace_id)

    def reset_ui_state(self, principal: Principal, request_id: str, trace_id: str) -> dict:
        return self.store.reset_ui_state(principal.org_id, principal.actor_id, request_id, trace_id)

    @staticmethod
    def _project_body(body: dict, include_revision: bool) -> tuple[dict, int | None]:
        required = {"title", "sponsor", "decision_owner", "affected_user", "desired_outcome", "documents", "status"}
        if include_revision:
            required.add("revision")
        if set(body) != required:
            raise ServiceError("project intake must contain exactly the documented fields")
        for field in ("title", "sponsor", "decision_owner", "affected_user"):
            if not isinstance(body[field], str) or not 1 <= len(body[field].strip()) <= 160:
                raise ServiceError(f"{field} must be 1..160 characters")
        if not isinstance(body["desired_outcome"], str) or not 1 <= len(body["desired_outcome"].strip()) <= 1000:
            raise ServiceError("desired_outcome must be 1..1000 characters")
        documents = body["documents"]
        if not isinstance(documents, dict) or set(documents) != PROJECT_DOCUMENTS \
                or any(type(value) is not bool for value in documents.values()):
            raise ServiceError("documents must contain the six required boolean controls")
        if body["status"] not in {"draft", "ready"}:
            raise ServiceError("project status must be draft or ready")
        if body["status"] == "ready" and not all(documents.values()):
            raise ServiceError("all intake documents must be complete before ready status")
        revision = body.get("revision")
        if include_revision and (isinstance(revision, bool) or not isinstance(revision, int) or revision < 1):
            raise ServiceError("project revision must be a positive integer")
        cleaned = {field: body[field].strip() if isinstance(body[field], str) else body[field]
                   for field in required - {"revision"}}
        return cleaned, revision

    def create_project(self, principal: Principal, body: dict, request_id: str,
                       trace_id: str) -> tuple[dict, bool]:
        _require(principal, *PROJECT_WRITERS)
        cleaned, _ = self._project_body(body, False)
        project_id = f"project-{secrets.token_hex(8)}"
        return self.store.create_project_intake(principal.org_id, principal.actor_id, project_id, cleaned,
                                                request_id, trace_id)

    def project(self, principal: Principal, project_id: str) -> dict:
        return self.store.project_intake(principal.org_id, project_id)

    def update_project(self, principal: Principal, project_id: str, body: dict,
                       request_id: str, trace_id: str) -> dict:
        _require(principal, *PROJECT_WRITERS)
        cleaned, revision = self._project_body(body, True)
        return self.store.update_project_intake(principal.org_id, principal.actor_id, project_id, cleaned,
                                                int(revision), request_id, trace_id)
