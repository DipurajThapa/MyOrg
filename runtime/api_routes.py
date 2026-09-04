#!/usr/bin/env python3
"""Every route this boundary answers, and the one place a refusal becomes a status code.

`_handle` owns the try/except: each route either sends its response and returns, or raises,
and the chain at the bottom turns each kind of refusal into exactly one status. Splitting a
route out of here without leaving it inside that try would give it a second, quieter answer
for the same failure, so both halves of the chain run under this one.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
from http import HTTPStatus

from runtime import triggers
from runtime.api_core import (CONSOLE_PAGE, LOG, RESOURCE_ID_RE, BadRequest, PayloadTooLarge, RouteNotFound,
                              TooManyRequests, UnsupportedMedia, WebhookDenied,
                              WEBHOOK_SECRET_SUFFIX)
from runtime.api_routes_ops import OperationsRoutesMixin
from runtime.auth import AuthError
from runtime.connectors import ConnectorError
from runtime.db import Conflict, NotFound, Store, StoreError
from runtime.service import Forbidden, ServiceError
from runtime.triggers import TriggerError


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


class RoutesMixin(OperationsRoutesMixin):
    """The request handler's routing half."""

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
            self._handle_operations(method, parts, path, principal)
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
