"""A person can stop a run (B-02).

Every other stop in the runtime is the machine stopping itself. `reject` was the only human
stop and it only works on a step already parked at a gate, so a run of green steps could not
be stopped at all. `cancel-run` ends any active run through `mutate`, keeps every artifact,
and names the human. The race experiment (docs/EXECUTION-TRACKER.md §5.2) is tests 1-3 here.
"""
from __future__ import annotations

import argparse
import hashlib
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


class CancelTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        keys = ("MYORG_RUNS_DIR", "MYORG_AUDIT_LOG")
        self._previous = {k: os.environ.get(k) for k in keys}
        self.addCleanup(self._restore)
        os.environ["MYORG_RUNS_DIR"] = self._tmp.name
        self.audit_log = Path(self._tmp.name) / "_audit-log.jsonl"
        os.environ["MYORG_AUDIT_LOG"] = str(self.audit_log)

        from runtime import company_runtime, escalation, executor, health, projection, scheduler
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        self.health = importlib.reload(health)
        self.escalation = importlib.reload(escalation)
        self.projection = importlib.reload(projection)
        self.scheduler = importlib.reload(scheduler)
        for module in (company_runtime, executor, health, escalation, projection, scheduler):
            self.addCleanup(lambda m=module: importlib.reload(m))
        self.addCleanup(self.clear_evidence)
        self.logs: list[str] = []

    def clear_evidence(self) -> None:
        for path in self.executor.EVIDENCE_DIR.glob("can-*"):
            path.unlink(missing_ok=True)

    def _restore(self) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def ns(self, **fields):
        return argparse.Namespace(**fields)

    def make_run(self, run_id: str, steps: int = 3, org: str = "default",
                 last_action: str = "draft") -> None:
        workflow = {
            "version": 1, "id": f"wf-{run_id}", "goal": f"stop study {run_id}",
            "max_cycles": 20,
            "steps": [{"id": f"s{n}", "owner": "cmo-marketing",
                       "action": last_action if n == steps else "draft",
                       "depends_on": [f"s{n - 1}"] if n > 1 else [], "max_attempts": 2}
                      for n in range(1, steps + 1)],
        }
        path = Path(self._tmp.name) / f"{run_id}.wf.json"
        path.write_text(json.dumps(workflow), encoding="utf-8")
        self.executor.quietly(self.core.create_run, self.ns(
            workflow=str(path), run_id=run_id, actor="chief-of-staff",
            request_id=f"create-{run_id}", org=org))

    def state(self, run_id: str) -> dict:
        return self.core.read_events(run_id)[-1]

    def cancel(self, run_id: str, approver: str = "Chief Operator",
               reason: str = "wrong goal", request_id: str | None = None) -> str:
        return self.executor.quietly(self.core.cancel_run, self.ns(
            run_id=run_id, approver=approver, reason=reason,
            request_id=request_id or f"cancel-{run_id}"))

    def audit_entries(self) -> list[dict]:
        if not self.audit_log.is_file():
            return []
        return [json.loads(line) for line in
                self.audit_log.read_text(encoding="utf-8").splitlines() if line.strip()]


class CancelMidFlight:
    """A backend that cancels the run 'from another process' while the first step runs."""

    def __init__(self, test: CancelTestBase, run_id: str):
        self.test, self.run_id, self.fired = test, run_id, False
        self.stub = test.executor.StubBackend()

    def __call__(self, request):
        if request.kind == "work" and request.run_id == self.run_id and not self.fired:
            self.fired = True
            self.test.cancel(self.run_id)
        return self.stub(request)


class CancelVerbTest(CancelTestBase):
    def test_a_green_only_run_in_flight_is_stopped_and_never_moves_again(self):
        self.make_run("can-live")
        # Put s1 in flight with a live claim, exactly as the driver would.
        self.executor.quietly(self.core.request_step, self.ns(
            run_id="can-live", step="s1", actor="cmo-marketing", holder="driver-a",
            request_id="req-1"))
        self.assertTrue(self.core.claim_is_live(self.state("can-live")["steps"]["s1"]))

        self.assertEqual(self.cancel("can-live"), "cancelled")

        state = self.state("can-live")
        self.assertEqual(state["run_status"], "cancelled")
        self.assertEqual(state["cancelled_by"], "Chief Operator")
        self.assertEqual(state["cancel_reason"], "wrong goal")
        self.assertFalse(self.core.claim_is_live(state["steps"]["s1"]))
        # Nothing moves it afterwards: the driver returns without touching it.
        seq = state["seq"]
        self.executor.advance("can-live", self.executor.StubBackend(), log=self.logs.append)
        self.assertEqual(self.state("can-live")["seq"], seq)
        self.assertTrue(any("nothing further will happen on its own" in line
                            for line in self.logs))

    def test_a_dispatch_that_returns_after_the_cancel_is_discarded_and_the_sweep_continues(self):
        self.make_run("can-race")
        self.make_run("can-other")
        backend = CancelMidFlight(self, "can-race")

        result = self.scheduler.sweep(backend, log=self.logs.append)

        self.assertTrue(backend.fired)
        raced = self.state("can-race")
        self.assertEqual(raced["run_status"], "cancelled")
        self.assertEqual(raced["steps"]["s1"]["status"], "in_progress")  # never completed
        self.assertEqual(raced["steps"]["s2"]["status"], "pending")
        self.assertNotIn("can-race", result.failed)  # a clean stop, not an error
        self.assertTrue(any("run ended while the agent was working" in line for line in self.logs))
        # The other run was driven to completion in the same pass.
        other = self.state("can-other")
        self.assertEqual(other["run_status"], "completed")
        self.assertEqual(sum(s["status"] == "completed" for s in other["steps"].values()), 3)

    def test_a_complete_that_bypasses_the_early_exit_is_refused_not_applied(self):
        """The belt behind the braces: even a caller that never checks the run status
        cannot write a step into a cancelled run."""
        self.make_run("can-late")
        self.executor.quietly(self.core.request_step, self.ns(
            run_id="can-late", step="s1", actor="cmo-marketing", holder="driver-a",
            request_id="req-1"))
        evidence = self.executor.write_evidence("can-late", "s1", "late work " * 30)
        self.cancel("can-late")
        with self.assertRaises(SystemExit) as stop:
            self.core.complete(self.ns(run_id="can-late", step="s1", actor="cmo-marketing",
                                       evidence=evidence, spend=0.0, claim_token=None,
                                       revision=self.state("can-late")["workflow_revision"],
                                       request_id="late-complete"))
        self.assertIn("run is terminal: cancelled", str(stop.exception))
        self.assertEqual(self.state("can-late")["steps"]["s1"]["status"], "in_progress")

    def test_evidence_written_before_the_cancel_survives_and_still_hashes(self):
        # Two green steps complete, then the run parks at a yellow one -- still active.
        self.make_run("can-keep", last_action="publish")
        self.executor.advance("can-keep", self.executor.StubBackend(), log=self.logs.append)
        state = self.state("can-keep")
        done = [s for s in state["steps"].values() if s["status"] == "completed"]
        self.assertEqual(len(done), 2)
        self.assertEqual(state["run_status"], "active")

        self.cancel("can-keep")

        for step in done:
            path = ROOT / step["evidence"]
            self.assertTrue(path.is_file(), f"{path} was deleted by the cancel")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             step["evidence_sha256"])
        # The chain is one event longer, not truncated.
        events = self.core.read_events("can-keep")
        self.assertEqual(events[-1]["event"], "run.cancelled")
        self.assertEqual(events[-1]["seq"], events[-2]["seq"] + 1)

    def test_a_cancel_needs_a_named_human_a_reason_and_an_active_run(self):
        self.make_run("can-guard")
        with self.assertRaises(SystemExit) as no_one:
            self.cancel("can-guard", approver="  ")
        self.assertIn("who did it", str(no_one.exception))
        with self.assertRaises(SystemExit) as no_why:
            self.cancel("can-guard", reason="")
        self.assertIn("reason", str(no_why.exception))
        self.assertEqual(self.state("can-guard")["run_status"], "active")

        self.cancel("can-guard")
        # Replay of the same request is a no-op that says so; a new request is refused
        # with a message that names the current status (REC-11).
        self.assertIn("idempotent replay", self.cancel("can-guard"))
        with self.assertRaises(SystemExit) as again:
            self.cancel("can-guard", request_id="cancel-again")
        self.assertIn("run is terminal: cancelled", str(again.exception))

    def test_the_audit_log_names_the_human_and_the_reason(self):
        self.make_run("can-audit")
        self.cancel("can-audit", approver="Chief Operator", reason="duplicate of can-1")
        entries = [e for e in self.audit_entries() if e["action"] == "run.cancelled"]
        human = next(e for e in entries if e["actor"] == "Chief Operator")
        self.assertEqual(human["category"], "yellow")
        self.assertEqual(human["approval"], "granted")
        self.assertIn("duplicate of can-1", human["note"])
        self.assertEqual(human["target"], "can-audit")

    def test_cancel_is_not_a_tool_any_agent_can_be_granted(self):
        grants = json.loads((ROOT / "runtime" / "tools.json").read_text(encoding="utf-8"))
        self.assertNotIn("cancel", json.dumps(grants).lower())


class EveryTerminalStateIsHandledTest(CancelTestBase):
    """A new terminal state must never fall through and read as 'running' (the race
    experiment saw exactly that before this change)."""

    def test_health_escalation_and_projection_agree_with_terminal_run(self):
        for status in self.core.TERMINAL_RUN - {"completed"}:
            state = {"run_status": status, "steps": {}, "cycle_count": 0, "max_cycles": 1}
            kind, _ = self.health.classify(state, idle=0)
            self.assertIn(kind, (self.health.FAILED, self.health.WAITING), status)
            if kind == self.health.FAILED:
                self.assertIn(status, self.escalation.DEAD_END, status)
            self.assertNotEqual(self.projection.coarse(status), "active", status)

    def test_every_terminal_transition_records_its_end_exactly_once(self):
        """Not only classified: *recorded*. `rejected_by_checker` was terminal in practice
        and absent from TERMINAL_RUN, so `record_terminal` never fired for it. This walks
        the canonical set through `mutate` and counts the audit line each one leaves."""
        for status in sorted(self.core.TERMINAL_RUN):
            run_id = f"can-end-{status.replace('_', '-')}"
            self.make_run(run_id, steps=1)

            def end(state, status=status):
                state["run_status"] = status
            self.core.mutate(run_id, f"end-{run_id}", f"run.{status}", "test", run_id, end)
            lines = [e for e in self.audit_entries()
                     if e["action"] == f"run.{status}" and e["target"] == run_id
                     and e["actor"] == "runtime"]
            self.assertEqual(len(lines), 1, status)
            self.assertEqual(lines[0]["outcome"], "ok" if status == "completed" else "blocked")
            with self.assertRaises(SystemExit):  # and it really is the end
                self.core.mutate(run_id, f"after-{run_id}", "step.requested", "test", run_id,
                                 lambda s: None)

    def test_no_verb_can_invent_a_run_status(self):
        """One canonical set, enforced at the one place every verb passes through."""
        self.make_run("can-bogus", steps=1)

        def invent(state):
            state["run_status"] = "paused_by_someone"
        with self.assertRaises(SystemExit) as refused:
            self.core.mutate("can-bogus", "bogus-1", "run.paused", "test", "can-bogus", invent)
        self.assertIn("not in TERMINAL_RUN", str(refused.exception))
        self.assertEqual(self.state("can-bogus")["run_status"], "active")

    def test_a_cancelled_run_reads_as_stopped_everywhere(self):
        self.make_run("can-seen")
        self.executor.quietly(self.core.request_step, self.ns(
            run_id="can-seen", step="s1", actor="cmo-marketing", holder="driver-a",
            request_id="req-1"))
        self.cancel("can-seen")
        report = self.health.health("can-seen")
        self.assertEqual(report.state, self.health.FAILED)
        self.assertIn("cancelled", report.detail)
        self.assertEqual(self.projection.coarse("cancelled"), "cancelled")
        self.assertNotIn("can-seen", self.scheduler.movable_runs())


class CancelServiceTest(CancelTestBase):
    """The Control Center's route: same authority and org scoping as a step decision."""

    def setUp(self) -> None:
        super().setUp()
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

    def principal(self, org: str = "acme", actor: str = "chief"):
        return self.tokens.verify(self.tokens.issue(org, actor))

    def test_a_decision_owner_stops_a_run_and_is_named_in_the_log(self):
        self.make_run("can-svc", org="acme")
        result = self.service.cancel_run(self.principal(), "can-svc",
                                         {"reason": "customer withdrew"}, "req-cancel-1")
        self.assertEqual(result, {"run_id": "can-svc", "status": "cancelled"})
        self.assertEqual(self.state("can-svc")["cancelled_by"], "Chief Operator")
        # Same request id twice is one cancel.
        self.assertEqual(self.service.cancel_run(self.principal(), "can-svc",
                                                 {"reason": "customer withdrew"}, "req-cancel-1"),
                         {"run_id": "can-svc", "status": "cancelled"})

    def test_viewers_agents_and_other_orgs_cannot(self):
        self.make_run("can-deny", org="acme")
        with self.assertRaises(Forbidden):
            self.service.cancel_run(self.principal(actor="watcher"), "can-deny",
                                    {"reason": "x"}, "r1")
        with self.assertRaises(Forbidden):
            self.service.cancel_run(self.principal(actor="robot"), "can-deny",
                                    {"reason": "x"}, "r2")
        with self.assertRaises(ServiceError) as other:
            self.service.cancel_run(self.principal(org="other"), "can-deny",
                                    {"reason": "x"}, "r3")
        with self.assertRaises(ServiceError) as missing:
            self.service.cancel_run(self.principal(), "can-nope", {"reason": "x"}, "r4")
        self.assertEqual(str(other.exception), str(missing.exception).replace("can-nope", "can-deny"))
        self.assertEqual(self.state("can-deny")["run_status"], "active")

    def test_the_body_is_exactly_one_reason(self):
        self.make_run("can-body", org="acme")
        for body in ({}, {"reason": ""}, {"reason": "x" * 201}, {"reason": "ok", "force": True},
                     {"reason": "two\nlines"}):
            with self.assertRaises(ServiceError, msg=body):
                self.service.cancel_run(self.principal(), "can-body", body, "r")
        self.assertEqual(self.state("can-body")["run_status"], "active")


if __name__ == "__main__":
    unittest.main()
