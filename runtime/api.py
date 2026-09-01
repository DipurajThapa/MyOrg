#!/usr/bin/env python3
"""Minimal authenticated HTTP boundary for the MyOrg production foundation."""
from __future__ import annotations

import json
import hmac
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from runtime.auth import AuthError, TokenService, bearer_token
from runtime.connectors import ConnectorError
from runtime.db import Conflict, NotFound, Store, StoreError
from runtime.gateway_auth import GatewayAuthenticator
from runtime.observability import JsonFormatter, Metrics
from runtime.service import Forbidden, MyOrgService, ServiceError

MAX_JSON_BYTES = 262_144
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
RESOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
LOG = logging.getLogger("myorg.api")


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
        self.metrics_token = metrics_token


class MyOrgHandler(BaseHTTPRequestHandler):
    server: MyOrgHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
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

    def _send_text(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self._response_status = int(status)
        self._security_headers()
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

    def _handle(self, method: str) -> None:
        try:
            parts, path = self._route()
            if method == "GET" and path == "/healthz":
                self._send(HTTPStatus.OK, {"status": "ok"})
                return
            if method == "GET" and path == "/readyz":
                verification = self.server.store.verify()
                self._send(HTTPStatus.OK, {"status": "ready", "database": verification})
                return
            if method == "GET" and path == "/metrics":
                supplied = self.headers.get("Authorization", "")
                expected = f"Bearer {self.server.metrics_token}" if self.server.metrics_token else ""
                if not expected or not hmac.compare_digest(supplied, expected):
                    raise RouteNotFound()
                self._send_text(HTTPStatus.OK, self.server.metrics.render(),
                                "text/plain; version=0.0.4; charset=utf-8")
                return
            principal = self._principal(method, path)
            if method == "GET" and path == "/v1/me":
                self._send(HTTPStatus.OK, {"actor_id": principal.actor_id, "actor_type": principal.actor_type,
                                           "display_name": principal.display_name, "org_id": principal.org_id,
                                           "roles": list(principal.roles)})
                return
            if method == "POST" and path == "/v1/runs":
                result, created = self.server.service.create_run(principal, self._json(), self._request_id())
                self._send(HTTPStatus.CREATED if created else HTTPStatus.OK, result)
                return
            if method == "GET" and len(parts) in {3, 4} and parts[:2] == ["v1", "runs"]:
                run_id = parts[2]
                if not RESOURCE_ID_RE.fullmatch(run_id):
                    raise BadRequest("invalid run id")
                if len(parts) == 3:
                    result = self.server.store.run(principal.org_id, run_id)
                elif parts[3] == "events":
                    result = self.server.store.run_events(principal.org_id, run_id)
                    for item in result:
                        item.pop("payload_json", None)
                else:
                    raise RouteNotFound()
                self._send(HTTPStatus.OK, result)
                return
            if method == "POST" and path == "/v1/approvals":
                result = self.server.service.request_approval(principal, self._json(), self._request_id())
                self._send(HTTPStatus.CREATED, result)
                return
            if method == "POST" and len(parts) == 4 and parts[:2] == ["v1", "approvals"] and parts[3] == "decision":
                result = self.server.service.decide_approval(principal, parts[2], self._json(), self._request_id())
                self._send(HTTPStatus.OK, result)
                return
            if method == "GET" and path == "/v1/connectors":
                self._send(HTTPStatus.OK, self.server.service.connector_inventory(principal))
                return
            if method == "GET" and path == "/v1/connectors/unreconciled":
                if not principal.has_role("system-admin", "auditor"):
                    raise Forbidden("role is not authorized for this operation")
                self._send(HTTPStatus.OK, self.server.store.unreconciled_connector_receipts(principal.org_id))
                return
            if method == "POST" and path == "/v1/connectors/fixture-execute":
                result, created = self.server.service.execute_fixture(
                    principal, self._json(), self._request_id("Idempotency-Key"))
                self._send(HTTPStatus.CREATED if created else HTTPStatus.OK, result)
                return
            if len(parts) == 4 and parts[:2] == ["v1", "connectors"] and parts[3] == "status" and method == "PUT":
                if not RESOURCE_ID_RE.fullmatch(parts[2]):
                    raise BadRequest("invalid connector id")
                result = self.server.service.set_connector_enabled(
                    principal, parts[2], self._json(), self._request_id(), self.trace_id)
                self._send(HTTPStatus.OK, result)
                return
            if len(parts) == 4 and parts[:2] == ["v1", "connectors"] and parts[3] == "authorization":
                if not RESOURCE_ID_RE.fullmatch(parts[2]):
                    raise BadRequest("invalid connector id")
                if method == "POST":
                    result = self.server.service.authorize_connector(
                        principal, parts[2], self._json(), self._request_id(), self.trace_id)
                    self._send(HTTPStatus.CREATED, result)
                    return
                if method == "DELETE":
                    result = self.server.service.revoke_connector_authorization(
                        principal, parts[2], self._request_id(), self.trace_id)
                    self._send(HTTPStatus.OK, result)
                    return
            if len(parts) == 4 and parts[:2] == ["v1", "connector-receipts"] \
                    and parts[3] == "reconciliation" and method == "POST":
                if not RESOURCE_ID_RE.fullmatch(parts[2]):
                    raise BadRequest("invalid receipt id")
                result = self.server.service.reconcile_connector_receipt(
                    principal, parts[2], self._json(), self._request_id(), self.trace_id)
                self._send(HTTPStatus.OK, result)
                return
            if method == "GET" and path == "/v1/ui-state":
                self._send(HTTPStatus.OK, self.server.service.ui_state(principal))
                return
            if method == "PUT" and path == "/v1/ui-state":
                result = self.server.service.save_ui_state(
                    principal, self._json(), self._request_id(), self.trace_id)
                self._send(HTTPStatus.OK, result)
                return
            if method == "DELETE" and path == "/v1/ui-state":
                result = self.server.service.reset_ui_state(
                    principal, self._request_id(), self.trace_id)
                self._send(HTTPStatus.OK, result)
                return
            if method == "POST" and path == "/v1/projects":
                result, created = self.server.service.create_project(
                    principal, self._json(), self._request_id("Idempotency-Key"), self.trace_id)
                self._send(HTTPStatus.CREATED if created else HTTPStatus.OK, result)
                return
            if len(parts) == 3 and parts[:2] == ["v1", "projects"]:
                project_id = parts[2]
                if not RESOURCE_ID_RE.fullmatch(project_id):
                    raise BadRequest("invalid project id")
                if method == "GET":
                    self._send(HTTPStatus.OK, self.server.service.project(principal, project_id))
                    return
                if method == "PUT":
                    result = self.server.service.update_project(
                        principal, project_id, self._json(), self._request_id(), self.trace_id)
                    self._send(HTTPStatus.OK, result)
                    return
            raise RouteNotFound()
        except AuthError as error:
            self._error(HTTPStatus.UNAUTHORIZED, "unauthorized", str(error))
        except TooManyRequests as error:
            self._error(HTTPStatus.TOO_MANY_REQUESTS, "rate_limited", str(error))
        except Forbidden as error:
            self._error(HTTPStatus.FORBIDDEN, "forbidden", str(error))
        except RouteNotFound:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "route not found")
        except NotFound as error:
            self._error(HTTPStatus.NOT_FOUND, "not_found", str(error))
        except Conflict as error:
            self._error(HTTPStatus.CONFLICT, "conflict", str(error))
        except UnsupportedMedia as error:
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type", str(error))
        except PayloadTooLarge as error:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "payload_too_large", str(error))
        except (BadRequest, ServiceError, ConnectorError) as error:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request", str(error))
        except StoreError:
            LOG.exception("storage failure path=%s", self.path)
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "storage verification failed")
        except Exception:
            LOG.exception("unexpected request failure path=%s", self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "request could not be completed")


class BadRequest(RuntimeError):
    pass


class PayloadTooLarge(RuntimeError):
    pass


class UnsupportedMedia(RuntimeError):
    pass


class TooManyRequests(RuntimeError):
    pass


class RouteNotFound(RuntimeError):
    pass


def create_server(host: str, port: int, database: str | Path, auth_secret: str,
                  allowed_origins: set[str] | None = None, gateway_secret: str | None = None,
                  metrics_token: str | None = None) -> MyOrgHTTPServer:
    store = Store(database)
    store.migrate()
    gateway = GatewayAuthenticator(store, gateway_secret) if gateway_secret else None
    return MyOrgHTTPServer((host, port), store, TokenService(store, auth_secret), allowed_origins,
                           gateway, metrics_token)


def main() -> int:
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
