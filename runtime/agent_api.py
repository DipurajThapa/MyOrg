#!/usr/bin/env python3
"""The door a worker outside this process uses to do a department's work.

Claim a step, get told what to do, send the result back, heartbeat while it takes a
while. Every rule that governs an in-process agent governs a caller here too: the
runtime decides ownership and risk, and this API cannot approve anything -- approvals
stay on the human console, by design.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402
from runtime import leases  # noqa: E402
from runtime.executor import (acceptance_failure, current_state,  # noqa: E402
                              hold_for_human, namespace, quietly,
                              record_failure, request_id, write_evidence)
from runtime.backends import ExecutorError  # noqa: E402
from runtime.health import all_health  # noqa: E402
from runtime.prompts import (StepRequest, agent_brief, structural_failure)  # noqa: E402

HOST = "127.0.0.1"
DEFAULT_PORT = 8788
TOKEN_ENV = "MYORG_AGENT_TOKEN"
MAX_BODY_BYTES = 1_000_000
CLAIMABLE = "ready"


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def token() -> str:
    value = os.environ.get(TOKEN_ENV, "")
    if len(value) < 32:
        raise SystemExit(
            f"{TOKEN_ENV} must be set to at least 32 characters before this API starts")
    return value


def authorized(header: str | None) -> bool:
    prefix = "Bearer "
    if not header or not header.startswith(prefix):
        return False
    return hmac.compare_digest(header[len(prefix):], token())


def field(body: dict, name: str) -> str:
    value = str(body.get(name, "")).strip()
    if not value:
        raise ApiError(400, f"{name} is required")
    return value


def open_work(agent: str | None = None) -> list[dict]:
    """Steps a worker could pick up right now, newest runs last."""
    offers = []
    for run in all_health():
        if run.state not in ("running", "stalled"):
            continue
        try:
            state = current_state(run.run_id)
        except SystemExit:
            continue
        for step_id, step in sorted(state["steps"].items()):
            if step["status"] != CLAIMABLE:
                continue
            if agent and step["owner"] != agent:
                continue
            offers.append({"run_id": run.run_id, "step": step_id,
                           "owner": step["owner"], "action": step["action"],
                           "risk": step["risk"], "goal": state["goal"]})
    return offers


def holder_for(agent: str) -> str:
    """One holder identity per outside worker, stable from claim to submit, and always
    distinct from an in-process driver's."""
    return f"api-{agent}"


def claim(body: dict) -> dict:
    """Take a step and get everything needed to do it. The runtime vets the claim."""
    from runtime.executor import last_feedback, remembered_for, upstream_handoffs
    run_id, step_id, agent = (field(body, "run_id"), field(body, "step"),
                              field(body, "agent"))
    state = current_state(run_id)
    step = state["steps"].get(step_id)
    if not step:
        raise ApiError(404, f"unknown step: {step_id}")
    held = leases.held_by(run_id, step_id)
    if held and not held.expired(datetime.now(timezone.utc)):
        raise ApiError(409, f"{step_id} is already held by {held.agent}")
    try:
        status = quietly(core.request_step, namespace(
            run_id=run_id, step=step_id, actor=agent, holder=holder_for(agent),
            request_id=request_id(step_id)))
    except SystemExit as error:
        raise ApiError(409, str(error)) from error
    if status != "in_progress":
        # Yellow and red steps stop for a human; a worker never receives them.
        return {"claimed": False, "status": status,
                "detail": "this step is gated and has been handed to a human"}
    lease = leases.grant(run_id, step_id, agent)
    fresh = current_state(run_id)
    request = StepRequest(
        run_id=run_id, step_id=step_id, agent=agent, action=step["action"],
        goal=fresh["goal"], brief=agent_brief(agent),
        handoffs=upstream_handoffs(fresh, fresh["steps"][step_id]),
        feedback=last_feedback(fresh, fresh["steps"][step_id]),
        remembered=remembered_for(step_id, fresh["steps"][step_id], fresh))
    return {"claimed": True, "status": status, "lease_expires_at": lease.expires_at,
            "revision": fresh["workflow_revision"], "prompt": request.prompt()}


def graded_failure(run_id, step_id, step, state, output) -> str | None:
    """Work sent in from outside is judged by the same criteria as work done in here.

    Grading needs a model. If it cannot answer, this raises rather than returning None:
    an unreachable grader is not a pass, and the caller parks the work for a person.
    """
    from runtime.backends import ClaudeCliBackend
    return acceptance_failure(run_id, step_id, step, state, ClaudeCliBackend(), output)


def submit(body: dict) -> dict:
    """Hand back the finished work. It faces the same quality gate as in-process work."""
    run_id, step_id, agent = (field(body, "run_id"), field(body, "step"),
                              field(body, "agent"))
    output = field(body, "output")
    held = leases.held_by(run_id, step_id)
    if held is None or held.agent != agent:
        raise ApiError(409, f"{agent} does not hold {step_id}")
    state = current_state(run_id)
    rejection = structural_failure(output)
    if rejection is None:
        try:
            rejection = graded_failure(run_id, step_id, state["steps"][step_id], state, output)
        except ExecutorError as error:
            # The gate could not run. Keep the worker's output, park the step for a
            # person, and tell the worker its job is done -- never record a silent pass.
            hold_for_human(run_id, step_id, agent, output, str(error), log=lambda _m: None)
            leases.release(run_id, step_id)
            raise ApiError(503, f"quality gate unavailable; {step_id} is held for a human")
    if rejection:
        record_failure(run_id, step_id, agent, rejection)
        leases.release(run_id, step_id)
        raise ApiError(422, rejection)
    evidence = write_evidence(run_id, step_id, output)
    try:
        run_status = quietly(core.complete, namespace(
            run_id=run_id, step=step_id, actor=agent, evidence=evidence,
            revision=state["workflow_revision"],
            claim_token=state["steps"][step_id].get("claim_token") or None,
            request_id=request_id(step_id)))
    except SystemExit as error:
        raise ApiError(409, str(error)) from error
    leases.release(run_id, step_id)
    return {"accepted": True, "evidence": evidence, "run_status": run_status}


def heartbeat(body: dict) -> dict:
    run_id, step_id, agent = (field(body, "run_id"), field(body, "step"),
                              field(body, "agent"))
    try:
        lease = leases.renew(run_id, step_id, agent)
    except SystemExit as error:
        raise ApiError(409, str(error)) from error
    return {"lease_expires_at": lease.expires_at}


def give_up(body: dict) -> dict:
    run_id, step_id, agent = (field(body, "run_id"), field(body, "step"),
                              field(body, "agent"))
    held = leases.held_by(run_id, step_id)
    if held is None or held.agent != agent:
        raise ApiError(409, f"{agent} does not hold {step_id}")
    record_failure(run_id, step_id, agent, field(body, "reason"))
    leases.release(run_id, step_id)
    return {"released": True}


ROUTES = {"/v1/claim": claim, "/v1/submit": submit,
          "/v1/heartbeat": heartbeat, "/v1/fail": give_up}


class Handler(BaseHTTPRequestHandler):
    server_version = "MyOrgAgentAPI/1.0"

    def log_message(self, *_args) -> None:
        pass

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _guard(self) -> bool:
        if authorized(self.headers.get("Authorization")):
            return True
        self._json({"error": "unauthorized"}, 401)
        return False

    def do_GET(self) -> None:
        if not self._guard():
            return
        path = self.path.split("?")[0]
        if path == "/v1/work":
            agent = None
            if "?" in self.path:
                from urllib.parse import parse_qs
                agent = parse_qs(self.path.split("?", 1)[1]).get("agent", [None])[0]
            self._json({"work": open_work(agent)})
        elif path == "/v1/health":
            self._json({"runs": [vars(run) for run in all_health()]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self._guard():
            return
        handler = ROUTES.get(self.path)
        if handler is None:
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self._json({"error": "body too large"}, 413)
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "body must be JSON"}, 400)
            return
        try:
            self._json(handler(body))
        except ApiError as error:
            self._json({"error": error.message}, error.status)
        except SystemExit as error:
            self._json({"error": str(error)}, 409)


def serve(port: int = DEFAULT_PORT) -> None:
    token()  # refuse to start without a real token
    server = ThreadingHTTPServer((HOST, port), Handler)
    print(f"Agent API: http://{HOST}:{server.server_address[1]}  (bearer token required)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve(parser.parse_args(argv).port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
