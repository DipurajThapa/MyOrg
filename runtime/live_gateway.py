#!/usr/bin/env python3
"""Reaching a real external system, with the unknown outcome treated as a first-class one.

A fixture call either works or raises. A real one has a third result: the bytes left, and
we never learned what happened. Treating that as a failure invites a retry that charges the
customer twice; treating it as a success invites a lie. So every live call writes its
intent *before* it leaves and settles it afterwards, and anything still in flight is handed
to a person rather than retried by a machine.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import socket
import ssl
import uuid
from urllib.parse import urlsplit

from runtime import audit
from runtime.connectors import ConnectorError, action_digest
from runtime.db import Conflict, Store, digest, utc_now

IN_FLIGHT = "in_flight"
ACCEPTED = "accepted"
FAILED = "failed"
MINIMUM_SECRET_LENGTH = 16
AMBIGUOUS_STATUSES = {408, 425, 429}
UNRESOLVED_HINT = ("a previous attempt with this idempotency key never resolved; a human must "
                   "reconcile it before this action is attempted again")
AUDIT_OUTCOME = {ACCEPTED: "executed", FAILED: "failed", IN_FLIGHT: "unresolved"}


class GatewayUnavailable(ConnectorError):
    """The call provably never reached the provider, so nothing happened out there."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def resolve_global_address(host: str, port: int) -> tuple[int, str]:
    """Resolve now and check every answer, so a name that was public when the connector was
    admitted cannot point at the internal network by the time the call is made."""
    try:
        candidates = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as error:
        raise GatewayUnavailable(f"connector host could not be resolved: {error}") from error
    if not candidates:
        raise GatewayUnavailable("connector host resolved to no addresses")
    for family, _type, _proto, _canonical, sockaddr in candidates:
        if not ipaddress.ip_address(sockaddr[0]).is_global:
            raise ConnectorError("connector host resolves to a non-public address")
    family, _type, _proto, _canonical, sockaddr = candidates[0]
    return family, sockaddr[0]


class HttpsTransport:
    """One request, to one pinned address, with no redirects and a hard response ceiling."""

    def __init__(self, context: ssl.SSLContext | None = None):
        self.context = context or ssl.create_default_context()

    def send(self, base_url: str, action: str, body: bytes, secret: str,
             timeout_seconds: int, max_response_bytes: int) -> dict:
        parsed = urlsplit(base_url)
        host = (parsed.hostname or "").lower()
        port = parsed.port or 443
        family, address = resolve_global_address(host, port)
        path = f"{parsed.path.rstrip('/')}/{action}"
        # Connect to the address we just checked, but keep verifying the certificate against
        # the name -- pinning the address without pinning the name would defeat TLS.
        connection = http.client.HTTPSConnection(
            address if family == socket.AF_INET else f"[{address}]", port,
            timeout=timeout_seconds, context=self.context)
        connection.host = host
        sent = False
        try:
            connection.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", host)
            connection.putheader("Authorization", f"Bearer {secret}")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(body)))
            connection.putheader("Accept", "application/json")
            connection.endheaders()
            connection.send(body)
            sent = True
            response = connection.getresponse()
            payload = response.read(max_response_bytes + 1)
            return {"status": int(response.status), "body": payload[:max_response_bytes],
                    "truncated": len(payload) > max_response_bytes}
        except (OSError, http.client.HTTPException, ssl.SSLError) as error:
            if not sent:
                raise GatewayUnavailable(
                    f"connector request never left this host: {type(error).__name__}") from error
            return {"status": None, "body": b"", "truncated": False,
                    "note": f"request sent, no response read: {type(error).__name__}"}
        finally:
            connection.close()


def classify(result: dict) -> tuple[str, str]:
    """Map a transport result onto a receipt status. Ambiguity is preserved, not rounded."""
    status = result.get("status")
    if status is None:
        return IN_FLIGHT, result.get("note", "request sent, no response read")
    if 200 <= status < 300:
        return ACCEPTED, f"provider accepted with {status}"
    if status in AMBIGUOUS_STATUSES or status >= 500:
        return IN_FLIGHT, f"provider returned {status}; whether it took effect is unknown"
    return FAILED, f"provider rejected with {status}"


def read_secret(secret_ref: str) -> str:
    """The manifest stores the *name* of the variable. The value never touches the database."""
    value = os.environ.get(secret_ref, "")
    if len(value) < MINIMUM_SECRET_LENGTH:
        raise ConnectorError("connector secret is absent or too short in this environment")
    return value


class LiveConnectorGateway:
    """Admission, human authorization, exact approval, one attempt, honest settlement."""

    def __init__(self, store: Store, transport: HttpsTransport | None = None):
        self.store = store
        self.transport = transport or HttpsTransport()

    def _admit(self, org_id: str, connector_id: str, action: str) -> dict:
        connector = self.store.connector(org_id, connector_id)
        if connector["kind"] == "fixture":
            raise ConnectorError("fixture connectors are executed through the fixture gateway")
        if not connector["enabled"]:
            raise ConnectorError("connector is not enabled")
        if connector["mode"] != "propose_write" or action not in connector["allowed_actions"]:
            raise ConnectorError("connector action is not allowed")
        authorization = self.store.connector_authorization(org_id, connector_id)
        if not authorization or authorization["status"] != "authorized":
            raise ConnectorError("connector has no current human authorization")
        host = (urlsplit(connector["base_url"]).hostname or "").lower().rstrip(".")
        if host not in connector["allowed_hosts"]:
            raise ConnectorError("connector base_url drifted outside its allowlist")
        return connector

    def _record(self, actor_id: str, connector_id: str, action: str, target_ref: str,
                approval_id: str, payload_sha256: str, outcome: str, note: str) -> None:
        audit.append(actor=actor_id, action=f"connector.{action}", category="yellow",
                     target=f"{connector_id}:{target_ref}", approval="granted",
                     evidence=f"sha256:{payload_sha256}", outcome=outcome,
                     note=f"approval {approval_id}: {note}")

    def execute(self, org_id: str, connector_id: str, action: str, target_ref: str,
                payload_ref: str, payload_sha256: str, approval_id: str, actor_id: str,
                idempotency_key: str, payload: dict) -> tuple[dict, bool]:
        connector = self._admit(org_id, connector_id, action)
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if sha256_bytes(body) != payload_sha256:
            raise ConnectorError("payload does not match the approved payload_sha256")
        exact_hash = action_digest(connector_id, action, target_ref, payload_ref, payload_sha256)
        request_hash = digest({"connector_id": connector_id, "action": action, "target_ref": target_ref,
                               "payload_ref": payload_ref, "payload_sha256": payload_sha256,
                               "action_hash": exact_hash})

        prior = self.store.connector_receipt(org_id, connector_id, idempotency_key)
        if prior:
            if prior["request_hash"] != request_hash:
                raise Conflict("connector idempotency key reused with a different request")
            if prior["status"] == IN_FLIGHT:
                raise Conflict(UNRESOLVED_HINT)
            return prior, False

        secret = read_secret(connector["secret_ref"])
        receipt = {"id": f"receipt-{uuid.uuid4().hex[:16]}", "connector_id": connector_id,
                   "idempotency_key": idempotency_key, "request_hash": request_hash,
                   "provider_receipt": "", "status": IN_FLIGHT, "created_at": utc_now()}
        # Approval is spent and the intent is durable before a single byte leaves. If this
        # process dies on the next line, the record already says "we may have acted".
        stored = self.store.consume_approval_and_record_receipt(
            org_id, approval_id, actor_id, exact_hash,
            f"consume-{connector_id}-{idempotency_key}", receipt)
        if stored["id"] != receipt["id"]:
            return stored, False
        self._record(actor_id, connector_id, action, target_ref, approval_id, payload_sha256,
                     "attempted", f"receipt {receipt['id']} in flight")

        try:
            result = self.transport.send(connector["base_url"], action, body, secret,
                                         connector["timeout_seconds"], connector["max_response_bytes"])
        except GatewayUnavailable as error:
            settled = self.store.settle_connector_receipt(
                org_id, receipt["id"], FAILED, "", "", str(error), actor_id)
            self._record(actor_id, connector_id, action, target_ref, approval_id, payload_sha256,
                         "failed", str(error))
            raise GatewayUnavailable(f"{error}; receipt {receipt['id']} settled as failed") from error

        status, note = classify(result)
        response_sha = sha256_bytes(result["body"])
        settled = self.store.settle_connector_receipt(
            org_id, receipt["id"], status, f"http:{result['status'] or 'none'}:{response_sha[:20]}",
            response_sha, note, actor_id)
        self._record(actor_id, connector_id, action, target_ref, approval_id, payload_sha256,
                     AUDIT_OUTCOME[status], note)
        return settled, True
