#!/usr/bin/env python3
"""Fail-closed connector admission, webhook verification, and fixture execution."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from runtime.db import Conflict, Store, canonical, digest, utc_now

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
ACTION_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
SECRET_REF_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ConnectorError(RuntimeError):
    pass


def validate_manifest(value: dict) -> dict:
    allowed_keys = {"id", "kind", "mode", "base_url", "allowed_hosts", "allowed_actions", "secret_ref",
                    "timeout_seconds", "max_response_bytes", "enabled"}
    if set(value) - allowed_keys:
        raise ConnectorError("connector manifest contains unknown fields")
    result = dict(value)
    if not ID_RE.fullmatch(str(result.get("id", ""))) or not ID_RE.fullmatch(str(result.get("kind", ""))):
        raise ConnectorError("connector id and kind must be lowercase slugs")
    if result.get("mode") not in {"disabled", "read_only", "propose_write"}:
        raise ConnectorError("invalid connector mode")
    if not isinstance(result.get("enabled"), bool):
        raise ConnectorError("enabled must be boolean")
    hosts = result.get("allowed_hosts")
    if not isinstance(hosts, list) or not hosts or any(not isinstance(item, str) for item in hosts):
        raise ConnectorError("allowed_hosts must be a non-empty list")
    hosts = sorted(set(item.lower().rstrip(".") for item in hosts))
    actions = result.get("allowed_actions")
    if not isinstance(actions, list) or not actions or any(not ACTION_RE.fullmatch(str(item)) for item in actions):
        raise ConnectorError("allowed_actions must contain lowercase slugs")
    secret_ref = result.get("secret_ref")
    if secret_ref is not None and not SECRET_REF_RE.fullmatch(str(secret_ref)):
        raise ConnectorError("secret_ref must be an environment-variable name, never a secret value")
    if result.get("kind") != "fixture" and secret_ref is None:
        raise ConnectorError("live connector manifests require a secret reference while remaining disabled")
    timeout = result.get("timeout_seconds")
    maximum = result.get("max_response_bytes")
    if not isinstance(timeout, int) or not 1 <= timeout <= 10:
        raise ConnectorError("timeout_seconds must be 1..10")
    if not isinstance(maximum, int) or not 1 <= maximum <= 1_048_576:
        raise ConnectorError("max_response_bytes must be 1..1048576")
    parsed = urlsplit(str(result.get("base_url", "")))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConnectorError("base_url must be a credential-free HTTPS origin")
    host = parsed.hostname.lower().rstrip(".")
    if parsed.port not in {None, 443} or host not in hosts:
        raise ConnectorError("base_url host and port must match the exact allowlist")
    if host == "localhost" or host.endswith(".local"):
        raise ConnectorError("local connector targets are not allowed")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ConnectorError("private, loopback, link-local, and reserved connector targets are denied")
    result["allowed_hosts"] = hosts
    result["allowed_actions"] = sorted(set(actions))
    return result


def action_digest(connector_id: str, action: str, target_ref: str, payload_ref: str, payload_sha256: str) -> str:
    if not SHA256_RE.fullmatch(payload_sha256):
        raise ConnectorError("payload_sha256 must be lowercase SHA-256")
    return digest({"connector_id": connector_id, "action": action, "target_ref": target_ref,
                   "payload_ref": payload_ref, "payload_sha256": payload_sha256})


class WebhookVerifier:
    def __init__(self, store: Store, maximum_skew_seconds: int = 300):
        self.store = store
        self.maximum_skew_seconds = maximum_skew_seconds

    def verify(self, org_id: str, connector_id: str, secret: bytes, timestamp: str, nonce: str,
               body: bytes, signature: str, now_epoch: int | None = None) -> None:
        if not 32 <= len(secret) <= 1024:
            raise ConnectorError("webhook secret length is invalid")
        if len(body) > 1_048_576:
            raise ConnectorError("webhook payload exceeds the limit")
        if not re.fullmatch(r"[0-9]{10}", timestamp) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", nonce):
            raise ConnectorError("webhook timestamp or nonce is invalid")
        current = int(time.time()) if now_epoch is None else int(now_epoch)
        observed = int(timestamp)
        if abs(current - observed) > self.maximum_skew_seconds:
            raise ConnectorError("webhook timestamp is outside the replay window")
        if not re.fullmatch(r"v1=[0-9a-f]{64}", signature):
            raise ConnectorError("webhook signature format is invalid")
        message = timestamp.encode("ascii") + b"." + nonce.encode("ascii") + b"." + body
        expected = "v1=" + hmac.new(secret, message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ConnectorError("webhook signature is invalid")
        expires = datetime.fromtimestamp(observed, timezone.utc) + timedelta(seconds=self.maximum_skew_seconds)
        self.store.record_webhook_nonce(org_id, connector_id, nonce, expires.isoformat(timespec="seconds").replace("+00:00", "Z"))


class FixtureConnectorGateway:
    """A deterministic adapter used to prove gateway controls without a live external system."""

    def __init__(self, store: Store):
        self.store = store

    def execute(self, org_id: str, connector_id: str, action: str, target_ref: str, payload_ref: str,
                payload_sha256: str, approval_id: str, actor_id: str, idempotency_key: str) -> tuple[dict, bool]:
        connector = self.store.connector(org_id, connector_id)
        if connector["kind"] != "fixture" or not connector["enabled"]:
            raise ConnectorError("only an enabled fixture connector is admitted in this release")
        if connector["mode"] != "propose_write" or action not in connector["allowed_actions"]:
            raise ConnectorError("connector action is not allowed")
        exact_hash = action_digest(connector_id, action, target_ref, payload_ref, payload_sha256)
        request = {"connector_id": connector_id, "action": action, "target_ref": target_ref,
                   "payload_ref": payload_ref, "payload_sha256": payload_sha256, "action_hash": exact_hash}
        request_hash = digest(request)
        prior = self.store.connector_receipt(org_id, connector_id, idempotency_key)
        if prior:
            if prior["request_hash"] != request_hash:
                raise Conflict("connector idempotency key reused with a different request")
            return prior, False
        receipt = {
            "id": f"receipt-{uuid.uuid4().hex[:16]}", "connector_id": connector_id,
            "idempotency_key": idempotency_key, "request_hash": request_hash,
            "provider_receipt": f"fixture:{digest(request)[:20]}", "status": "accepted", "created_at": utc_now(),
        }
        result = self.store.consume_approval_and_record_receipt(
            org_id, approval_id, actor_id, exact_hash,
            f"consume-{connector_id}-{idempotency_key}", receipt,
        )
        return result, result["id"] == receipt["id"]
