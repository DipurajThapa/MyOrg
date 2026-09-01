#!/usr/bin/env python3
"""Verify signed requests from an authenticated UI gateway and bind them to DB actors."""
from __future__ import annotations

import hashlib
import hmac
import re
import time

from runtime.auth import AuthError, Principal
from runtime.db import Conflict, NotFound, Store

NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
SIGNATURE_RE = re.compile(r"^v1=[0-9a-f]{64}$")
ISSUER_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")


class GatewayAuthenticator:
    def __init__(self, store: Store, secret: str | bytes, audience: str = "myorg-api", maximum_skew_seconds: int = 60):
        raw = secret.encode("utf-8") if isinstance(secret, str) else secret
        if len(raw) < 32:
            raise AuthError("MYORG_GATEWAY_SECRET must contain at least 32 bytes")
        self.store = store
        self.secret = raw
        self.audience = audience
        self.maximum_skew_seconds = maximum_skew_seconds

    @staticmethod
    def message(method: str, path: str, timestamp: str, nonce: str, issuer: str,
                subject: str, audience: str, body: bytes) -> bytes:
        body_hash = hashlib.sha256(body).hexdigest()
        return "\n".join((method.upper(), path, timestamp, nonce, issuer, subject, audience, body_hash)).encode("utf-8")

    def verify(self, method: str, path: str, body: bytes, headers, now_epoch: int | None = None) -> Principal:
        issuer = headers.get("X-MyOrg-Gateway-Issuer", "")
        subject = headers.get("X-MyOrg-Gateway-Subject", "").strip().lower()
        audience = headers.get("X-MyOrg-Gateway-Audience", "")
        timestamp = headers.get("X-MyOrg-Gateway-Timestamp", "")
        nonce = headers.get("X-MyOrg-Gateway-Nonce", "")
        signature = headers.get("X-MyOrg-Gateway-Signature", "")
        if not ISSUER_RE.fullmatch(issuer) or not subject or len(subject) > 320:
            raise AuthError("invalid gateway identity")
        if audience != self.audience or not timestamp.isdigit() or len(timestamp) != 10:
            raise AuthError("invalid gateway audience or timestamp")
        if not NONCE_RE.fullmatch(nonce) or not SIGNATURE_RE.fullmatch(signature):
            raise AuthError("invalid gateway nonce or signature")
        current = int(time.time()) if now_epoch is None else int(now_epoch)
        observed = int(timestamp)
        if abs(current - observed) > self.maximum_skew_seconds:
            raise AuthError("gateway request is outside the replay window")
        expected = "v1=" + hmac.new(
            self.secret, self.message(method, path, timestamp, nonce, issuer, subject, audience, body), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise AuthError("invalid gateway signature")
        try:
            identity = self.store.identity(issuer, subject)
            self.store.record_gateway_nonce(issuer, nonce, observed + self.maximum_skew_seconds)
            actor = self.store.actor(identity["org_id"], identity["actor_id"])
        except (NotFound, Conflict) as error:
            raise AuthError("gateway identity is not authorized or request was replayed") from error
        return Principal(
            org_id=actor["org_id"], actor_id=actor["id"], actor_type=actor["actor_type"],
            display_name=actor["display_name"], roles=tuple(actor["roles"]), jti=f"gateway:{nonce}",
        )
