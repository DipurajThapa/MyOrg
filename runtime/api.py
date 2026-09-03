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

from runtime import triggers
from runtime.auth import AuthError, TokenService, bearer_token
from runtime.connectors import ConnectorError
from runtime.db import Conflict, NotFound, Store, StoreError
from runtime.gateway_auth import GatewayAuthenticator
from runtime.observability import JsonFormatter, Metrics, RuntimeGauges
from runtime.service import Forbidden, MyOrgService, ServiceError
from runtime.triggers import TriggerError

WEBHOOK_SECRET_SUFFIX = "_WEBHOOK_SECRET"

MAX_JSON_BYTES = 262_144
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
RESOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
LOG = logging.getLogger("myorg.api")
CONSOLE_PAGE = Path(__file__).with_name("console.html")


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


class MyOrgHandler(BaseHTTPRequestHandler):
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

    def _console_actor(self) -> str:
        """The console is off unless a human is named for it, and it never answers anything
        but the loopback interface -- a remote request must still present a token."""
        actor_id = os.environ.get("MYORG_CONSOLE_ACTOR", "").strip()
        if not actor_id or self.client_address[0] not in {"127.0.0.1", "::1"}:
            raise RouteNotFound()
        return actor_id

    def _send_console(self) -> None:
        actor_id = self._console_actor()
        self._actor_context = f"console:{actor_id}"
        nonce = secrets.token_urlsafe(16)
        page = CONSOLE_PAGE.read_text(encoding="utf-8").replace("__NONCE__", nonce)
        self._send_text(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8",
                        f"default-src 'none'; script-src 'nonce-{nonce}'; "
                        f"style-src 'nonce-{nonce}'; connect-src 'self'; "
                        "frame-ancestors 'none'; base-uri 'none'")

    def _send_console_token(self) -> None:
        actor_id = self._console_actor()
        org_id = os.environ.get("MYORG_CONSOLE_ORG", "default").strip()
        self._actor_context = f"{org_id}:{actor_id}"
        ttl = 600
        # `issue` is the same call the admin CLI makes, and it enforces the same things:
        # the actor must exist, be active, and belong to an organization that is not
        # suspended. The console gets no authority the CLI does not already grant.
        token = self.server.tokens.issue(org_id, actor_id, ttl_seconds=ttl)
        self._send(HTTPStatus.OK, {"token": token, "expires_in": ttl})

    def _webhook(self, org_id: str, connector_id: str) -> None:
        """Signed inbound event. Everything about this route is deliberately narrow: it does
        no planning, spends no tokens, reads exactly one field of the payload, and answers
        the same way whether the trigger is unknown or the signature is wrong -- so it
        cannot be used to enumerate what this company listens for."""
        if not RESOURCE_ID_RE.fullmatch(org_id) or not RESOURCE_ID_RE.fullmatch(connector_id):
            raise BadRequest("invalid organization or connector id")
        if not self.server.rate_limiter.allow(f"webhook:{org_id}:{connector_id}"):
            raise TooManyRequests("rate limit exceeded")
        self._actor_context = f"{org_id}:webhook:{connector_id}"
        body = self._body_bytes()
        secret = webhook_secret(self.server.store, org_id, connector_id)
        try:
            if self.server.store.organization_status(org_id) != "active":
                # Suspended means suspended (B-03) -- and the refusal is the same one every
                # other rejection gets, so the route still cannot be used to enumerate orgs.
                raise TriggerError("organization is suspended")
            intake, created = triggers.receive_webhook(
                self.server.store, org_id, connector_id, secret,
                self.headers.get("X-MyOrg-Timestamp", ""), self.headers.get("X-MyOrg-Nonce", ""),
                self.headers.get("X-MyOrg-Signature", ""), body)
        except (ConnectorError, TriggerError, Conflict, NotFound) as error:
            LOG.info(json.dumps({"event": "webhook.rejected", "org": org_id,
                                 "connector": connector_id, "reason": str(error)},
                                separators=(",", ":"), sort_keys=True))
            raise WebhookDenied() from error
        self._send(HTTPStatus.ACCEPTED if created else HTTPStatus.OK,
                   {"accepted": True, "intake_id": intake["id"], "status": intake["status"]})

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
            # The operator console: one page, and the short-lived token it runs on. Both
            # are refused unless MYORG_CONSOLE_ACTOR names a human and the caller is on the
            # loopback interface -- the same trust boundary as the admin CLI, which anyone
            # who can already read the database and the signing secret has anyway.
            if method == "GET" and path in {"/", "/console"}:
                self._send_console()
                return
            if method == "GET" and path == "/v1/console/token":
                self._send_console_token()
                return
            if method == "GET" and path == "/metrics":
                supplied = self.headers.get("Authorization", "")
                expected = f"Bearer {self.server.metrics_token}" if self.server.metrics_token else ""
                if not expected or not hmac.compare_digest(supplied, expected):
                    raise RouteNotFound()
                # Both halves in one scrape: the web boundary, and the company running
                # itself. Reporting only the first is what OBS-08 was about.
                self._send_text(HTTPStatus.OK,
                                self.server.metrics.render() + self.server.runtime_gauges.render(),
                                "text/plain; version=0.0.4; charset=utf-8")
                return
            # The one route with no bearer token: an outside system cannot hold one. It
            # authenticates by HMAC over the exact bytes instead, and is deliberately
            # handled before `_principal` so a signature can never be mistaken for a login.
            if method == "POST" and len(parts) == 4 and parts[:2] == ["v1", "webhooks"]:
                self._webhook(parts[2], parts[3])
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
            if method == "GET" and path == "/v1/runs":
                self._send(HTTPStatus.OK, self.server.service.runs(principal))
                return
            if method == "GET" and len(parts) in {3, 4} and parts[:2] == ["v1", "runs"]:
                run_id = parts[2]
                if not RESOURCE_ID_RE.fullmatch(run_id):
                    raise BadRequest("invalid run id")
                if len(parts) == 3:
                    result = self.server.store.run(principal.org_id, run_id)
                elif parts[3] == "output":
                    result = self.server.service.run_output(principal, run_id)
                elif parts[3] == "events":
                    result = self.server.store.run_events(principal.org_id, run_id)
                    for item in result:
                        item.pop("payload_json", None)
                else:
                    raise RouteNotFound()
                self._send(HTTPStatus.OK, result)
                return
            if method == "POST" and path == "/v1/ideas":
                result = self.server.service.submit_idea(principal, self._json(), self._request_id())
                self._send(HTTPStatus.ACCEPTED if result["created"] else HTTPStatus.OK, result)
                return
            if method == "GET" and path == "/v1/ideas":
                self._send(HTTPStatus.OK, self.server.service.ideas(principal))
                return
            if method == "GET" and path == "/v1/decisions":
                self._send(HTTPStatus.OK, self.server.service.pending_decisions(principal))
                return
            if method == "POST" and len(parts) == 4 and parts[:2] == ["v1", "decisions"]:
                run_id, step_id = parts[2], parts[3]
                if not RESOURCE_ID_RE.fullmatch(run_id) or not RESOURCE_ID_RE.fullmatch(step_id):
                    raise BadRequest("invalid run or step id")
                result = self.server.service.decide_step(principal, run_id, step_id,
                                                         self._json(), self._request_id())
                self._send(HTTPStatus.OK, result)
                return
            if method == "GET" and path == "/v1/memory/proposals":
                self._send(HTTPStatus.OK, self.server.service.memory_proposals(principal))
                return
            if method == "POST" and len(parts) == 4 and parts[:2] == ["v1", "memory"] and parts[3] == "decision":
                if not RESOURCE_ID_RE.fullmatch(parts[2]):
                    raise BadRequest("invalid memory entry id")
                result = self.server.service.decide_memory(principal, parts[2], self._json(), self._request_id())
                self._send(HTTPStatus.OK, result)
                return
            if method == "POST" and len(parts) == 4 and parts[:2] == ["v1", "runs"] and parts[3] == "cancel":
                if not RESOURCE_ID_RE.fullmatch(parts[2]):
                    raise BadRequest("invalid run id")
                result = self.server.service.cancel_run(principal, parts[2], self._json(), self._request_id())
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
            if method == "POST" and path == "/v1/connectors/execute":
                result, created = self.server.service.execute_live(
                    principal, self._json(), self._request_id("Idempotency-Key"))
                self._send(HTTPStatus.CREATED if created else HTTPStatus.OK, result)
                return
            if method == "GET" and path == "/v1/connectors/in-flight":
                self._send(HTTPStatus.OK, self.server.service.in_flight_receipts(principal))
                return
            if method == "POST" and path == "/v1/triggers/webhook":
                result = self.server.service.register_webhook_trigger(
                    principal, self._json(), self._request_id(), self.trace_id)
                self._send(HTTPStatus.CREATED, result)
                return
            if path == "/v1/schedules":
                if method == "GET":
                    self._send(HTTPStatus.OK, self.server.service.schedules(principal))
                    return
                if method == "POST":
                    result = self.server.service.create_schedule(
                        principal, self._json(), self._request_id(), self.trace_id)
                    self._send(HTTPStatus.CREATED, result)
                    return
            if method == "PUT" and len(parts) == 4 and parts[:2] == ["v1", "schedules"] \
                    and parts[3] == "status":
                if not RESOURCE_ID_RE.fullmatch(parts[2]):
                    raise BadRequest("invalid schedule id")
                result = self.server.service.set_schedule_enabled(
                    principal, parts[2], self._json(), self._request_id(), self.trace_id)
                self._send(HTTPStatus.OK, result)
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
        except WebhookDenied:
            self._error(HTTPStatus.FORBIDDEN, "webhook_denied", "webhook was not accepted")
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


def webhook_secret(store: Store, org_id: str, connector_id: str) -> bytes:
    """The inbound signing secret is a separate variable from the outbound bearer token: one
    is what the provider proves to us, the other is what we prove to the provider, and
    reusing one key for both would let either side forge the other's traffic."""
    try:
        connector = store.connector(org_id, connector_id)
    except NotFound as error:
        raise WebhookDenied() from error
    reference = connector.get("secret_ref")
    value = os.environ.get(f"{reference}{WEBHOOK_SECRET_SUFFIX}", "") if reference else ""
    if not value:
        raise WebhookDenied()
    return value.encode("utf-8")


class BadRequest(RuntimeError):
    pass


class WebhookDenied(RuntimeError):
    """One answer for every inbound rejection, so the route leaks nothing about what exists."""


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
