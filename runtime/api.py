#!/usr/bin/env python3
"""Minimal authenticated HTTP boundary for the MyOrg production foundation.

This file is the server and the request plumbing: the rate limiter, the socket, the
security headers, how a body is read and a response is written, and how a caller
becomes a principal. The routes themselves live in `api_routes`, and the constants and
refusals both halves share live in `api_core`.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from runtime.auth import AuthError, TokenService, bearer_token
from runtime.db import Store
from runtime.gateway_auth import GatewayAuthenticator
from runtime.observability import JsonFormatter, Metrics, RuntimeGauges
from runtime.service import MyOrgService

from runtime.api_core import (LOG, MAX_JSON_BYTES, REQUEST_ID_RE, BadRequest,
                              PayloadTooLarge, RouteNotFound, TooManyRequests,
                              UnsupportedMedia, WebhookDenied)
from runtime.api_routes import RoutesMixin, webhook_secret

__all__ = ["BadRequest", "MyOrgHandler", "MyOrgHTTPServer", "PayloadTooLarge", "RateLimiter",
           "RouteNotFound", "TooManyRequests", "UnsupportedMedia", "WebhookDenied",
           "create_server", "main", "webhook_secret"]


class RateLimiter:
    def __init__(self, maximum: int = 120, window_seconds: int = 60):
        self.maximum = maximum
        self.window_seconds = window_seconds
        self.items: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            observed = self.items[key]
            while observed and observed[0] <= now - self.window_seconds:
                observed.popleft()
            if len(observed) >= self.maximum:
                return False
            observed.append(now)
            return True


class MyOrgHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: Store, tokens: TokenService,
                 allowed_origins: set[str] | None = None, gateway: GatewayAuthenticator | None = None,
                 metrics_token: str | None = None):
        super().__init__(address, MyOrgHandler)
        self.store = store
        self.tokens = tokens
        self.service = MyOrgService(store)
        self.allowed_origins = allowed_origins or set()
        self.rate_limiter = RateLimiter()
        self.gateway = gateway
        self.metrics = Metrics()
        self.runtime_gauges = RuntimeGauges(store)
        self.metrics_token = metrics_token


class MyOrgHandler(RoutesMixin, BaseHTTPRequestHandler):
    server: MyOrgHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _security_headers(self, content_security_policy: str | None = None) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", content_security_policy
                         or "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("X-Frame-Options", "DENY")
        if os.environ.get("MYORG_BEHIND_TLS") == "1":
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        origin = self.headers.get("Origin")
        if origin and origin in self.server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _send(self, status: int, payload: dict | list) -> None:
        data = (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
        self.send_response(status)
        self._response_status = int(status)
        self._security_headers()
        self.send_header("X-Trace-Id", getattr(self, "trace_id", "unavailable"))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, status: int, data: bytes, content_type: str,
                   content_security_policy: str | None = None) -> None:
        self.send_response(status)
        self._response_status = int(status)
        self._security_headers(content_security_policy)
        self.send_header("X-Trace-Id", getattr(self, "trace_id", "unavailable"))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, code: str, message: str) -> None:
        self._send(status, {"error": {"code": code, "message": message}})

    def _principal(self, method: str, path: str):
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            principal = self.server.tokens.verify(bearer_token(authorization))
        elif self.server.gateway and self.headers.get("X-MyOrg-Gateway-Signature"):
            raw = self._body_bytes() if method in {"POST", "PUT"} else b""
            principal = self.server.gateway.verify(method, path, raw, self.headers)
        else:
            raise AuthError("authenticated bearer token or signed gateway request is required")
        if not self.server.rate_limiter.allow(f"{principal.org_id}:{principal.actor_id}"):
            raise TooManyRequests("rate limit exceeded")
        self._actor_context = f"{principal.org_id}:{principal.actor_id}"
        return principal

    def _request_id(self, header: str = "X-Request-Id") -> str:
        value = self.headers.get(header, "")
        if not REQUEST_ID_RE.fullmatch(value):
            raise BadRequest(f"{header} must be 8..128 safe characters")
        return value

    def _body_bytes(self) -> bytes:
        if hasattr(self, "_cached_body"):
            return self._cached_body
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise UnsupportedMedia("Content-Type must be application/json")
        raw_length = self.headers.get("Content-Length")
        if not raw_length or not raw_length.isdigit():
            raise BadRequest("Content-Length is required")
        length = int(raw_length)
        if length <= 0 or length > MAX_JSON_BYTES:
            raise PayloadTooLarge("JSON body is empty or exceeds 256 KiB")
        raw = self.rfile.read(length)
        self._cached_body = raw
        return raw

    def _json(self) -> dict:
        raw = self._body_bytes()
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise BadRequest("body must be valid UTF-8 JSON") from error
        if not isinstance(value, dict):
            raise BadRequest("JSON body must be an object")
        return value

    def _route(self) -> tuple[list[str], str]:
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            raise BadRequest("query strings are not accepted")
        return [part for part in parsed.path.split("/") if part], parsed.path

    def do_OPTIONS(self) -> None:
        origin = self.headers.get("Origin")
        if not origin or origin not in self.server.allowed_origins:
            self._error(HTTPStatus.FORBIDDEN, "origin_denied", "origin is not allowed")
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._security_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Request-Id, X-Trace-Id, Idempotency-Key")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        self.trace_id = self.headers.get("X-Trace-Id", "")
        if not REQUEST_ID_RE.fullmatch(self.trace_id):
            self.trace_id = secrets.token_hex(16)
        self._response_status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._actor_context = None
        started = time.monotonic()
        self.server.metrics.begin()
        try:
            self._handle(method)
        finally:
            elapsed = time.monotonic() - started
            self.server.metrics.observe(method, int(self._response_status), elapsed)
            LOG.info(json.dumps({"event": "http.request", "method": method,
                                 "path": urlsplit(self.path).path, "status": int(self._response_status),
                                 "duration_ms": round(elapsed * 1000, 3), "trace_id": self.trace_id,
                                 "actor": self._actor_context}, separators=(",", ":"), sort_keys=True))


def create_server(host: str, port: int, database: str | Path, auth_secret: str,
                  allowed_origins: set[str] | None = None, gateway_secret: str | None = None,
                  metrics_token: str | None = None) -> MyOrgHTTPServer:
    store = Store(database)
    store.migrate()
    gateway = GatewayAuthenticator(store, gateway_secret) if gateway_secret else None
    return MyOrgHTTPServer((host, port), store, TokenService(store, auth_secret), allowed_origins,
                           gateway, metrics_token)


def main() -> int:
    # A service has no console. Redirect before anything can print, so a refusal below --
    # a missing secret, a port already taken -- lands in the file rather than nowhere: this
    # process is started by a scheduled task under pythonw.exe, where stdout is discarded
    # and a silent exit is indistinguishable from a running server.
    log_file = os.environ.get("MYORG_API_LOG_FILE")
    if log_file:
        handle = open(log_file, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stderr = handle
        print(f"api starting at {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
              f" pid={os.getpid()}")
    host = os.environ.get("MYORG_HOST", "127.0.0.1")
    port = int(os.environ.get("MYORG_PORT", "8080"))
    if host not in {"127.0.0.1", "::1", "localhost"} and os.environ.get("MYORG_BEHIND_TLS") != "1":
        raise SystemExit("refusing a non-loopback bind unless MYORG_BEHIND_TLS=1")
    secret = os.environ.get("MYORG_AUTH_SECRET")
    if not secret:
        raise SystemExit("MYORG_AUTH_SECRET is required")
    database = os.environ.get("MYORG_DB", "runtime/data/myorg.db")
    origins = {item.strip() for item in os.environ.get("MYORG_ALLOWED_ORIGINS", "").split(",") if item.strip()}
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=os.environ.get("MYORG_LOG_LEVEL", "INFO"), handlers=[handler], force=True)
    server = create_server(host, port, database, secret, origins,
                           os.environ.get("MYORG_GATEWAY_SECRET"), os.environ.get("MYORG_METRICS_TOKEN"))
    LOG.info("listening host=%s port=%s", host, server.server_address[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
