"""The third human decision, on the one surface (B-09, A-07).

Lessons agents propose were decided on a loopback page with no identity. That page is
gone; `POST /v1/memory/{id}/decision` binds the decision to a registered human, a role, an
organization and a reason, exactly like a step decision -- and the repository carries no
other way to approve anything.
"""
from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from runtime.auth import TokenService
from runtime.db import Store
from runtime.service import Forbidden, MyOrgService, ServiceError

ROOT = Path(__file__).resolve().parents[1]
SECRET = "0123456789abcdef0123456789abcdef"


class MemoryDecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._previous = {k: os.environ.get(k) for k in ("MYORG_MEMORY_DIR", "MYORG_RUNS_DIR")}
        self.addCleanup(self._restore)
        os.environ["MYORG_MEMORY_DIR"] = self._tmp.name
        os.environ["MYORG_RUNS_DIR"] = self._tmp.name
        from runtime import memory
        self.memory = importlib.reload(memory)
        self.addCleanup(lambda: importlib.reload(memory))

        self.store = Store(Path(self._tmp.name) / "myorg.db")
        self.store.migrate()
        for org in ("acme", "other"):
            self.store.bootstrap_organization(org, org.title())
        self.store.upsert_actor("acme", "chief", "human", "Chief Operator", ["decision-owner"])
        self.store.upsert_actor("acme", "watcher", "human", "Watcher", ["viewer"])
        self.store.upsert_actor("acme", "robot", "agent", "Robot", ["decision-owner"])
        self.store.upsert_actor("other", "chief", "human", "Other Chief", ["decision-owner"])
        self.tokens = TokenService(self.store, SECRET)
        self.service = MyOrgService(self.store)

    def _restore(self) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def principal(self, org: str = "acme", actor: str = "chief"):
        return self.tokens.verify(self.tokens.issue(org, actor))

    def propose(self, subject: str = "cfo-finance on analyze work", org: str = "acme"):
        return self.memory.propose(subject, "Always name the period the figures cover.",
                                   author="coo-operations", org_id=org,
                                   source_run="run-1", source_step="s2")

    def records(self, org: str = "acme") -> list[dict]:
        path = Path(self._tmp.name) / f"{org}.memory.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def test_proposals_are_listed_for_the_caller_s_organization_only(self):
        mine = self.propose()
        self.propose(org="other")
        listed = self.service.memory_proposals(self.principal())
        self.assertEqual([p["id"] for p in listed], [mine.id])
        self.assertEqual(listed[0]["source_run"], "run-1")
        self.assertEqual(listed[0]["kind"], "lesson")

    def test_keeping_a_proposal_makes_it_live_and_names_the_human(self):
        entry = self.propose()
        result = self.service.decide_memory(self.principal(), entry.id,
                                            {"decision": "keep", "reason": "true every quarter"},
                                            "req-1")
        self.assertEqual(result, {"id": entry.id, "decision": "keep", "status": "approved",
                                  "decided_by": "Chief Operator"})
        live = [e for e in self.memory.current("acme") if e.live]
        self.assertEqual([e.id for e in live], [entry.id])
        self.assertEqual(live[0].note, "true every quarter")
        self.assertEqual(self.service.memory_proposals(self.principal()), [])
        # And it now reaches the agents.
        self.assertTrue(self.memory.recall("analyze the finance figures", "acme"))

    def test_discarding_a_proposal_keeps_it_out_of_every_prompt(self):
        entry = self.propose()
        result = self.service.decide_memory(self.principal(), entry.id,
                                            {"decision": "discard", "reason": "too vague"}, "r")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.memory.recall("analyze the finance figures", "acme"), [])

    def test_a_repeated_decision_is_harmless(self):
        entry = self.propose()
        for _ in range(2):
            self.service.decide_memory(self.principal(), entry.id,
                                       {"decision": "keep", "reason": "yes"}, "same")
        self.assertEqual(len([e for e in self.memory.current("acme") if e.id == entry.id]), 1)
        self.assertEqual(self.memory.current("acme")[0].status, "approved")

    def test_viewers_agents_and_other_orgs_cannot_decide(self):
        entry = self.propose()
        body = {"decision": "keep", "reason": "x"}
        with self.assertRaises(Forbidden):
            self.service.decide_memory(self.principal(actor="watcher"), entry.id, body, "r1")
        with self.assertRaises(Forbidden):
            self.service.decide_memory(self.principal(actor="robot"), entry.id, body, "r2")
        with self.assertRaises(ServiceError):  # another org's store has no such entry
            self.service.decide_memory(self.principal(org="other"), entry.id, body, "r3")
        self.assertEqual(self.memory.current("acme")[0].status, "proposed")

    def test_the_body_is_exactly_a_decision_and_a_reason(self):
        entry = self.propose()
        for body in ({}, {"decision": "keep"}, {"decision": "approve", "reason": "x"},
                     {"decision": "keep", "reason": ""}, {"decision": "keep", "reason": "x" * 201},
                     {"decision": "keep", "reason": "ok", "extra": 1}):
            with self.assertRaises(ServiceError, msg=body):
                self.service.decide_memory(self.principal(), entry.id, body, "r")
        with self.assertRaises(ServiceError):
            self.service.decide_memory(self.principal(), "mem-doesnotexist",
                                       {"decision": "keep", "reason": "x"}, "r")

    def test_the_route_exists_and_the_old_console_does_not(self):
        # Every file the HTTP boundary is split across, so moving a route between them
        # cannot quietly turn this guard off -- it is the route existing that matters here,
        # not which file holds it.
        source = "".join(path.read_text(encoding="utf-8")
                         for path in sorted((ROOT / "runtime").glob("api*.py")))
        self.assertIn('"/v1/memory/proposals"', source)
        self.assertIn('["v1", "memory"]', source)
        self.assertFalse((ROOT / "runtime" / "approval_server.py").exists(),
                         "the unauthenticated approvals console must stay gone")
        # No verb anywhere lets an agent's own tools approve, decide or remember.
        grants = (ROOT / "runtime" / "tools.json").read_text(encoding="utf-8").lower()
        for word in ("approve", "decide", "memory"):
            self.assertNotIn(word, grants)


if __name__ == "__main__":
    unittest.main()
