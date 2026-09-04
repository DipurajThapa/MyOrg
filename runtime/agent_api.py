#!/usr/bin/env python3
"""The door a worker outside this process uses to do a department"s work.

Claim a step, get told what to do, send the result back, heartbeat while it takes a
while. Every rule that governs an in-process agent governs a caller here too: the
runtime decides ownership and risk, and this API cannot approve anything -- approvals
stay on the human console, by design.

The verbs live in `agent_work`; this file is the HTTP boundary around them and is
re-exported from here so `from runtime import agent_api` still reaches both.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402
from runtime.executor import (acceptance_failure, current_state,  # noqa: E402
                              hold_for_human, namespace, quietly,
                              record_failure, write_evidence)
from runtime.backends import ExecutorError  # noqa: E402
from runtime.health import all_health  # noqa: E402
from runtime.prompts import (StepRequest, agent_brief, structural_failure)  # noqa: E402

HOST = "127.0.0.1"
DEFAULT_PORT = 8788
TOKEN_ENV = "MYORG_AGENT_TOKEN"
MAX_BODY_BYTES = 1_000_000
CLAIMABLE = "ready"


import importlib  # noqa: E402

from runtime import agent_work as _work  # noqa: E402

# Tests reload this module to pick up a changed MYORG_* value, and one replaces a name that
# `submit` calls. Both live in agent_work now, so reload the source first -- otherwise the
# reload reads back the old values and the cleanup never undoes the replacement.
if globals().get("_SOURCES_BOUND"):
    importlib.reload(_work)
_SOURCES_BOUND = True

from runtime.agent_work import (ApiError, CLAIMABLE, DEFAULT_PORT, HOST, MAX_BODY_BYTES, ROOT,
                                ROUTES, TOKEN_ENV, authorized, claim, field, give_up,
                                graded_failure, heartbeat, holder_for, holding, open_work,
                                request_id, submit, token)

__all__ = ["ApiError", "CLAIMABLE", "DEFAULT_PORT", "HOST", "MAX_BODY_BYTES", "ROOT", "ROUTES", "TOKEN_ENV", "authorized", "claim", "field", "give_up", "graded_failure", "heartbeat", "holder_for", "holding", "open_work", "request_id", "submit", "token", "Handler", "main", "serve"]


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
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self._json({"error": "body too large"}, 413)
            return
        # Read the body before answering anything, including a 404. Replying with bytes
        # still unread on the socket makes some clients see the connection aborted
        # instead of the status -- it showed up as a flaky test under load.
        raw = self.rfile.read(length)
        handler = ROUTES.get(self.path)
        if handler is None:
            self._json({"error": "not found"}, 404)
            return
        try:
            body = json.loads(raw or b"{}")
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
