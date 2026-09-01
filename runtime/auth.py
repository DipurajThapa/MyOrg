#!/usr/bin/env python3
"""Short-lived signed service identities with database-bound roles."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from runtime.db import NotFound, Store

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
HEADER = {"alg": "HS256", "kid": "local-v1", "typ": "JWT"}


class AuthError(RuntimeError):
    pass


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise AuthError("invalid token encoding")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as error:
        raise AuthError("invalid token encoding") from error
    if _b64encode(decoded) != value:
        raise AuthError("non-canonical token encoding")
    return decoded


def _json_segment(value: dict) -> str:
    return _b64encode(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


@dataclass(frozen=True)
class Principal:
    org_id: str
    actor_id: str
    actor_type: str
    display_name: str
    roles: tuple[str, ...]
    jti: str

    def has_role(self, *roles: str) -> bool:
        return bool(set(roles) & set(self.roles))


class TokenService:
    def __init__(self, store: Store, secret: str | bytes, issuer: str = "myorg-local",
                 audience: str = "myorg-api"):
        """`secret` may carry a second, comma-separated key: `current,previous`.

        Tokens are always signed with the first. Verification tries the rest too, which is
        the whole of key rotation: set both, wait one token lifetime (max 15 minutes), drop
        the old one. Without the overlap, rotating -- including rotating *because* a key
        leaked -- logs everybody out at once, so the safe move becomes the disruptive one.
        """
        raw = secret.encode("utf-8") if isinstance(secret, str) else secret
        keys = [part.strip() for part in raw.split(b",") if part.strip()]
        if not keys or any(len(key) < 32 for key in keys):
            raise AuthError("MYORG_AUTH_SECRET must contain at least 32 bytes")
        if len(keys) > 2:
            raise AuthError("MYORG_AUTH_SECRET accepts at most a current and a previous key")
        self.store = store
        self.secret = keys[0]
        self.accepted = keys
        self.issuer = issuer
        self.audience = audience

    def issue(self, org_id: str, actor_id: str, ttl_seconds: int = 300, now_epoch: int | None = None) -> str:
        if not 1 <= ttl_seconds <= 900:
            raise AuthError("token lifetime must be 1..900 seconds")
        actor = self.store.actor(org_id, actor_id)
        if actor["status"] != "active":
            raise AuthError("actor is disabled")
        now_value = int(time.time()) if now_epoch is None else int(now_epoch)
        payload = {
            "aud": self.audience,
            "exp": now_value + ttl_seconds,
            "iat": now_value,
            "iss": self.issuer,
            "jti": secrets.token_hex(16),
            "org": org_id,
            "sub": actor_id,
        }
        encoded_header = _json_segment(HEADER)
        encoded_payload = _json_segment(payload)
        signature = hmac.new(self.secret, f"{encoded_header}.{encoded_payload}".encode("ascii"), hashlib.sha256).digest()
        return f"{encoded_header}.{encoded_payload}.{_b64encode(signature)}"

    def verify(self, token: str, now_epoch: int | None = None) -> Principal:
        if len(token) > 4096:
            raise AuthError("token is too large")
        parts = token.split(".")
        if len(parts) != 3:
            raise AuthError("invalid token format")
        encoded_header, encoded_payload, encoded_signature = parts
        try:
            header = json.loads(_b64decode(encoded_header))
            payload = json.loads(_b64decode(encoded_payload))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise AuthError("invalid token JSON") from error
        if header != HEADER:
            raise AuthError("unsupported token header")
        signed = f"{encoded_header}.{encoded_payload}".encode("ascii")
        supplied = _b64decode(encoded_signature)
        if not any(hmac.compare_digest(hmac.new(key, signed, hashlib.sha256).digest(), supplied)
                   for key in self.accepted):
            raise AuthError("invalid token signature")
        required = {"aud", "exp", "iat", "iss", "jti", "org", "sub"}
        if set(payload) != required:
            raise AuthError("invalid token claims")
        now_value = int(time.time()) if now_epoch is None else int(now_epoch)
        if payload["iss"] != self.issuer or payload["aud"] != self.audience:
            raise AuthError("invalid token issuer or audience")
        if not isinstance(payload["iat"], int) or not isinstance(payload["exp"], int):
            raise AuthError("invalid token time claims")
        if payload["iat"] > now_value + 30 or payload["exp"] <= now_value or payload["exp"] - payload["iat"] > 900:
            raise AuthError("token is expired or outside the allowed lifetime")
        if not ID_RE.fullmatch(str(payload["org"])) or not ID_RE.fullmatch(str(payload["sub"])):
            raise AuthError("invalid token subject")
        if not re.fullmatch(r"[0-9a-f]{32}", str(payload["jti"])):
            raise AuthError("invalid token identifier")
        if self.store.token_revoked(payload["org"], payload["jti"]):
            raise AuthError("token is revoked")
        try:
            actor = self.store.actor(payload["org"], payload["sub"])
        except NotFound as error:
            raise AuthError("actor is not registered") from error
        if actor["status"] != "active":
            raise AuthError("actor is disabled")
        return Principal(
            org_id=actor["org_id"], actor_id=actor["id"], actor_type=actor["actor_type"],
            display_name=actor["display_name"], roles=tuple(actor["roles"]), jti=payload["jti"],
        )

    def revoke(self, token: str) -> None:
        principal = self.verify(token)
        payload = json.loads(_b64decode(token.split(".")[1]))
        expiry = datetime.fromtimestamp(payload["exp"], timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        self.store.revoke_token(principal.org_id, principal.jti, expiry)


def bearer_token(header: str | None) -> str:
    if not header or not header.startswith("Bearer "):
        raise AuthError("bearer token required")
    token = header[7:].strip()
    if not token or " " in token:
        raise AuthError("invalid bearer token")
    return token
