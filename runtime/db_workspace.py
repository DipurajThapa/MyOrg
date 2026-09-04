#!/usr/bin/env python3
"""What a signed-in person sees and the project intake packs they own."""
from __future__ import annotations

import json
import sqlite3

from runtime.db_core import Conflict, NotFound, canonical, digest, utc_now


class WorkspaceMixin:
    """What a signed-in person sees and the project intake packs they own."""

    @staticmethod
    def _default_ui_state(org_id: str, actor_id: str) -> dict:
        return {"org_id": org_id, "actor_id": actor_id, "schema_version": 1, "active_view": "overview",
                "time_range": "30d", "filters": {"queue": "all", "flow": "future"},
                "sort": {"queue": "updated_desc"}, "scroll_position": 0,
                "current_project_id": None, "revision": 0, "updated_at": None}

    def ui_state(self, org_id: str, actor_id: str) -> dict:
        self.actor(org_id, actor_id)
        with self.reading() as connection:
            row = connection.execute("SELECT * FROM ui_states WHERE org_id=? AND actor_id=?", (org_id, actor_id)).fetchone()
        if not row:
            return self._default_ui_state(org_id, actor_id)
        result = dict(row)
        result["filters"] = json.loads(result.pop("filters_json"))
        result["sort"] = json.loads(result.pop("sort_json"))
        return result

    def save_ui_state(self, org_id: str, actor_id: str, state: dict, expected_revision: int,
                      request_id: str, trace_id: str) -> dict:
        timestamp = utc_now()
        with self.transaction() as connection:
            prior = connection.execute("SELECT revision FROM ui_states WHERE org_id=? AND actor_id=?", (org_id, actor_id)).fetchone()
            actual = int(prior["revision"]) if prior else 0
            if actual != expected_revision:
                raise Conflict("UI state revision is stale")
            revision = actual + 1
            connection.execute(
                "INSERT INTO ui_states(org_id,actor_id,schema_version,active_view,time_range,filters_json,sort_json,scroll_position,current_project_id,revision,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(org_id,actor_id) DO UPDATE SET schema_version=excluded.schema_version,active_view=excluded.active_view,time_range=excluded.time_range,filters_json=excluded.filters_json,sort_json=excluded.sort_json,scroll_position=excluded.scroll_position,current_project_id=excluded.current_project_id,revision=excluded.revision,updated_at=excluded.updated_at",
                (org_id, actor_id, 1, state["active_view"], state["time_range"], canonical(state["filters"]),
                 canonical(state["sort"]), state["scroll_position"], state.get("current_project_id"), revision, timestamp),
            )
            self._append_operational_event(connection, org_id, actor_id, "ui", "state.saved", "ui_state",
                                           actor_id, request_id, trace_id, {"revision": revision})
        return self.ui_state(org_id, actor_id)

    def reset_ui_state(self, org_id: str, actor_id: str, request_id: str, trace_id: str) -> dict:
        with self.transaction() as connection:
            connection.execute("DELETE FROM ui_states WHERE org_id=? AND actor_id=?", (org_id, actor_id))
            self._append_operational_event(connection, org_id, actor_id, "ui", "state.reset", "ui_state",
                                           actor_id, request_id, trace_id, {})
        return self._default_ui_state(org_id, actor_id)

    def create_project_intake(self, org_id: str, actor_id: str, project_id: str, body: dict,
                              request_id: str, trace_id: str) -> tuple[dict, bool]:
        request_hash = digest(body)
        timestamp = utc_now()
        with self.transaction() as connection:
            prior = connection.execute(
                "SELECT operation,request_hash,resource_id FROM idempotency_requests WHERE org_id=? AND request_id=?",
                (org_id, request_id),
            ).fetchone()
            if prior:
                if prior["operation"] != "project.create" or prior["request_hash"] != request_hash:
                    raise Conflict("idempotency key reused with a different request")
                return self._project(connection, org_id, prior["resource_id"]), False
            connection.execute(
                "INSERT INTO project_intakes(id,org_id,title,sponsor,decision_owner,affected_user,desired_outcome,documents_json,status,revision,created_by,updated_by,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (project_id, org_id, body["title"], body["sponsor"], body["decision_owner"], body["affected_user"],
                 body["desired_outcome"], canonical(body["documents"]), body["status"], 1, actor_id, actor_id, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO idempotency_requests(org_id,request_id,operation,request_hash,resource_id,created_at) VALUES(?,?,?,?,?,?)",
                (org_id, request_id, "project.create", request_hash, project_id, timestamp),
            )
            self._append_operational_event(connection, org_id, actor_id, "project", "intake.created", "project",
                                           project_id, f"project-event-{request_id}", trace_id, {"status": body["status"]})
            return self._project(connection, org_id, project_id), True

    def _project(self, connection: sqlite3.Connection, org_id: str, project_id: str) -> dict:
        row = connection.execute("SELECT * FROM project_intakes WHERE org_id=? AND id=?", (org_id, project_id)).fetchone()
        if not row:
            raise NotFound("project intake not found")
        result = dict(row)
        result["documents"] = json.loads(result.pop("documents_json"))
        return result

    def project_intake(self, org_id: str, project_id: str) -> dict:
        with self.reading() as connection:
            return self._project(connection, org_id, project_id)

    def update_project_intake(self, org_id: str, actor_id: str, project_id: str, body: dict,
                              expected_revision: int, request_id: str, trace_id: str) -> dict:
        timestamp = utc_now()
        with self.transaction() as connection:
            prior = self._project(connection, org_id, project_id)
            if int(prior["revision"]) != expected_revision:
                raise Conflict("project intake revision is stale")
            revision = expected_revision + 1
            updated = connection.execute(
                "UPDATE project_intakes SET title=?,sponsor=?,decision_owner=?,affected_user=?,desired_outcome=?,documents_json=?,status=?,revision=?,updated_by=?,updated_at=? WHERE org_id=? AND id=? AND revision=?",
                (body["title"], body["sponsor"], body["decision_owner"], body["affected_user"], body["desired_outcome"],
                 canonical(body["documents"]), body["status"], revision, actor_id, timestamp, org_id, project_id, expected_revision),
            )
            if updated.rowcount != 1:
                raise Conflict("project intake revision is stale")
            self._append_operational_event(connection, org_id, actor_id, "project", "intake.updated", "project",
                                           project_id, request_id, trace_id, {"revision": revision, "status": body["status"]})
            return self._project(connection, org_id, project_id)
