#!/usr/bin/env python3
"""What an operator does outside a run: keep a lesson, register a trigger, schedule
work, save their view of the console, file a project."""
from __future__ import annotations

from runtime.auth import Principal
import secrets
from runtime import triggers

from runtime.service_core import Forbidden, ID_RE, PROJECT_DOCUMENTS, PROJECT_WRITERS, ServiceError, _require


class OperatorServiceMixin:
    """What an operator does outside a run: keep a lesson, register a trigger, schedule
work, save their view of the console, file a project."""

    def memory_proposals(self, principal: Principal) -> list[dict]:
        """Lessons and facts agents want the company to keep, waiting on a person."""
        from runtime import memory
        return [{"id": e.id, "kind": e.kind, "subject": e.subject, "body": e.body,
                 "author": e.author, "source_run": e.source_run, "source_step": e.source_step,
                 "proposed_at": e.ts}
                for e in memory.proposals(org_id=principal.org_id)]

    def decide_memory(self, principal: Principal, entry_id: str, body: dict,
                      request_id: str) -> dict:
        """Keep or discard a proposal, as a named human, with a stated reason (B-09).

        This is the third human decision the API carries. The first two act on a *run*
        (`decide_step` moves a parked workflow step) or on a *connector action*
        (`decide_approval` unlocks one exact outward call). This one changes what every
        future agent is told, so it takes the same authority as a step decision."""
        _require(principal, "decision-owner")
        if principal.actor_type != "human":
            raise Forbidden("memory decisions require a registered human identity")
        if set(body) != {"decision", "reason"} or body["decision"] not in {"keep", "discard"}:
            raise ServiceError("decision must be keep/discard with a reason")
        reason = str(body["reason"]).strip()
        if not 1 <= len(reason) <= 200 or not reason.isprintable():
            raise ServiceError("reason must be 1..200 printable characters on one line")
        if not ID_RE.fullmatch(str(entry_id)):
            raise ServiceError("invalid memory entry id")
        from runtime import memory
        who = principal.display_name or principal.actor_id
        status = memory.LIVE if body["decision"] == "keep" else "rejected"
        try:
            entry = memory.decide(entry_id, status, who, org_id=principal.org_id, note=reason)
        except SystemExit as error:
            raise ServiceError(str(error)) from error
        return {"id": entry.id, "decision": body["decision"], "status": entry.status,
                "decided_by": entry.decided_by}
    def register_webhook_trigger(self, principal: Principal, body: dict,
                                 request_id: str, trace_id: str) -> dict:
        _require(principal, "system-admin")
        if principal.actor_type != "human":
            raise Forbidden("registering a trigger requires a registered human identity")
        if set(body) != {"connector_id", "event_type", "goal", "enabled"} \
                or type(body["enabled"]) is not bool:
            raise ServiceError("trigger requires connector_id, event_type, goal and enabled")
        if not ID_RE.fullmatch(str(body["connector_id"])) \
                or not triggers.EVENT_TYPE_RE.fullmatch(str(body["event_type"])):
            raise ServiceError("connector_id and event_type must be slugs")
        goal = str(body["goal"]).strip()
        if not 1 <= len(goal) <= 500:
            raise ServiceError("goal must be 1..500 characters")
        return self.store.register_webhook_trigger(
            principal.org_id, body["connector_id"], body["event_type"], goal,
            body["enabled"], principal.actor_id, request_id, trace_id)

    def create_schedule(self, principal: Principal, body: dict, request_id: str, trace_id: str) -> dict:
        _require(principal, "system-admin")
        if principal.actor_type != "human":
            raise Forbidden("creating a schedule requires a registered human identity")
        if set(body) != {"id", "kind", "interval_seconds", "daily_at", "goal"}:
            raise ServiceError("schedule requires id, kind, interval_seconds, daily_at and goal")
        if not ID_RE.fullmatch(str(body["id"])) or body["kind"] not in {"interval", "daily"}:
            raise ServiceError("schedule id must be a slug and kind must be interval or daily")
        goal = str(body["goal"]).strip()
        if not 1 <= len(goal) <= 500:
            raise ServiceError("goal must be 1..500 characters")
        interval, daily = body["interval_seconds"], body["daily_at"]
        if body["kind"] == "interval" and (type(interval) is not int or daily is not None):
            raise ServiceError("interval schedules need interval_seconds and a null daily_at")
        if body["kind"] == "daily" and (interval is not None or not isinstance(daily, str)):
            raise ServiceError("daily schedules need daily_at and a null interval_seconds")
        try:
            following = triggers.next_fire(body["kind"], triggers.utc_now(), interval, daily)
        except triggers.TriggerError as error:
            raise ServiceError(str(error)) from error
        return self.store.create_schedule(
            principal.org_id, body["id"], body["kind"], goal, triggers.stamp(following),
            principal.actor_id, request_id, trace_id, interval, daily)

    def schedules(self, principal: Principal) -> list[dict]:
        _require(principal, "system-admin", "auditor", "chief-of-staff")
        return self.store.schedules(principal.org_id)

    def set_schedule_enabled(self, principal: Principal, schedule_id: str, body: dict,
                             request_id: str, trace_id: str) -> dict:
        _require(principal, "system-admin")
        if principal.actor_type != "human":
            raise Forbidden("pausing or resuming a schedule requires a registered human identity")
        if set(body) != {"enabled"} or type(body["enabled"]) is not bool:
            raise ServiceError("schedule status requires exactly one boolean enabled field")
        return self.store.set_schedule_enabled(principal.org_id, schedule_id, body["enabled"],
                                               principal.actor_id, request_id, trace_id)
    def ui_state(self, principal: Principal) -> dict:
        return self.store.ui_state(principal.org_id, principal.actor_id)

    def save_ui_state(self, principal: Principal, body: dict, request_id: str, trace_id: str) -> dict:
        required = {"schema_version", "active_view", "time_range", "filters", "sort", "scroll_position",
                    "current_project_id", "revision"}
        if set(body) != required or body["schema_version"] != 1:
            raise ServiceError("UI state must match schema version 1 exactly")
        if body["active_view"] not in {"overview", "intake", "queue", "flow", "autonomy"}:
            raise ServiceError("invalid active view")
        if body["time_range"] not in {"7d", "30d", "90d", "all"}:
            raise ServiceError("invalid time range")
        if body["filters"] not in ({"queue": "all", "flow": "current"},
                                   {"queue": "all", "flow": "future"},
                                   {"queue": "attention", "flow": "current"},
                                   {"queue": "attention", "flow": "future"}):
            raise ServiceError("invalid UI filters")
        if body["sort"] not in ({"queue": "updated_desc"}, {"queue": "updated_asc"}):
            raise ServiceError("invalid UI sort")
        if isinstance(body["scroll_position"], bool) or not isinstance(body["scroll_position"], int) \
                or not 0 <= body["scroll_position"] <= 10_000_000:
            raise ServiceError("scroll position must be an integer in range")
        project_id = body["current_project_id"]
        if project_id is not None and not ID_RE.fullmatch(str(project_id)):
            raise ServiceError("invalid current project id")
        revision = body["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ServiceError("UI state revision must be a non-negative integer")
        state = {key: body[key] for key in required - {"revision"}}
        return self.store.save_ui_state(principal.org_id, principal.actor_id, state, revision, request_id, trace_id)

    def reset_ui_state(self, principal: Principal, request_id: str, trace_id: str) -> dict:
        return self.store.reset_ui_state(principal.org_id, principal.actor_id, request_id, trace_id)

    @staticmethod
    def _project_body(body: dict, include_revision: bool) -> tuple[dict, int | None]:
        required = {"title", "sponsor", "decision_owner", "affected_user", "desired_outcome", "documents", "status"}
        if include_revision:
            required.add("revision")
        if set(body) != required:
            raise ServiceError("project intake must contain exactly the documented fields")
        for field in ("title", "sponsor", "decision_owner", "affected_user"):
            if not isinstance(body[field], str) or not 1 <= len(body[field].strip()) <= 160:
                raise ServiceError(f"{field} must be 1..160 characters")
        if not isinstance(body["desired_outcome"], str) or not 1 <= len(body["desired_outcome"].strip()) <= 1000:
            raise ServiceError("desired_outcome must be 1..1000 characters")
        documents = body["documents"]
        if not isinstance(documents, dict) or set(documents) != PROJECT_DOCUMENTS \
                or any(type(value) is not bool for value in documents.values()):
            raise ServiceError("documents must contain the six required boolean controls")
        if body["status"] not in {"draft", "ready"}:
            raise ServiceError("project status must be draft or ready")
        if body["status"] == "ready" and not all(documents.values()):
            raise ServiceError("all intake documents must be complete before ready status")
        revision = body.get("revision")
        if include_revision and (isinstance(revision, bool) or not isinstance(revision, int) or revision < 1):
            raise ServiceError("project revision must be a positive integer")
        cleaned = {field: body[field].strip() if isinstance(body[field], str) else body[field]
                   for field in required - {"revision"}}
        return cleaned, revision

    def create_project(self, principal: Principal, body: dict, request_id: str,
                       trace_id: str) -> tuple[dict, bool]:
        _require(principal, *PROJECT_WRITERS)
        cleaned, _ = self._project_body(body, False)
        project_id = f"project-{secrets.token_hex(8)}"
        return self.store.create_project_intake(principal.org_id, principal.actor_id, project_id, cleaned,
                                                request_id, trace_id)

    def project(self, principal: Principal, project_id: str) -> dict:
        return self.store.project_intake(principal.org_id, project_id)

    def update_project(self, principal: Principal, project_id: str, body: dict,
                       request_id: str, trace_id: str) -> dict:
        _require(principal, *PROJECT_WRITERS)
        cleaned, revision = self._project_body(body, True)
        return self.store.update_project_intake(principal.org_id, principal.actor_id, project_id, cleaned,
                                                int(revision), request_id, trace_id)
