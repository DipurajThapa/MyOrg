#!/usr/bin/env python3
"""Asking to touch the outside world, and the record of having touched it."""
from __future__ import annotations

from runtime.auth import Principal
from runtime.connectors import FixtureConnectorGateway, action_digest
from datetime import datetime, timedelta, timezone
import secrets

from runtime.service_core import Forbidden, ID_RE, REF_RE, SCOPE_RE, SECRET_REF_RE, SHA256_RE, ServiceError, _policy, _require


class ConnectorsServiceMixin:
    """Asking to touch the outside world, and the record of having touched it."""

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
