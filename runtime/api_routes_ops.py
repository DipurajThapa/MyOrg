#!/usr/bin/env python3
"""The second half of the route chain: connectors, triggers, schedules, and workspace.

Reached from `_handle`, which owns the try/except this runs inside, so a refusal raised
here is answered by the same one place that answers every other refusal. It ends by
raising `RouteNotFound`, which is why `_handle` has nothing after the call.
"""
from __future__ import annotations

from http import HTTPStatus

from runtime.api_core import RESOURCE_ID_RE, BadRequest, RouteNotFound
from runtime.service import Forbidden


class OperationsRoutesMixin:
    """Routes an operator or an integration uses, as opposed to the ones a run uses."""

    def _handle_operations(self, method: str, parts: list[str], path: str, principal) -> None:
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
