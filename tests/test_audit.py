"""The audit log must be produced by the runtime, not by an agent choosing to write it.

Every test here writes to a temporary log via MYORG_AUDIT_LOG, so the real
logs/audit-log.jsonl is never touched.
"""
from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AuditWriterTest(unittest.TestCase):
    """The store itself: nine fields, a hash chain, and tamper evidence."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = Path(self._tmp.name) / "audit-log.jsonl"
        self._previous = os.environ.get("MYORG_AUDIT_LOG")
        os.environ["MYORG_AUDIT_LOG"] = str(self.log)
        self.addCleanup(self._restore)
        from runtime import audit
        self.audit = audit

    def _restore(self) -> None:
        if self._previous is None:
            os.environ.pop("MYORG_AUDIT_LOG", None)
        else:
            os.environ["MYORG_AUDIT_LOG"] = self._previous

    def entries(self) -> list[dict]:
        if not self.log.is_file():
            return []
        return [json.loads(line) for line in
                self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def append(self, **overrides):
        fields = {"actor": "cmo-marketing", "action": "publish", "category": "yellow",
                  "target": "run-x/step-y", "approval": "pending",
                  "evidence": "logs/README.md", "outcome": "awaiting-approval",
                  "note": "parked for a human decision"}
        fields.update(overrides)
        return self.audit.append(**fields)

    def test_an_entry_records_all_nine_fields(self) -> None:
        self.append()
        written = self.entries()[-1]
        for field in ("ts", "actor", "action", "category", "target",
                      "approval", "evidence", "outcome", "note"):
            self.assertIn(field, written)

    def test_each_entry_chains_to_the_one_before_it(self) -> None:
        self.append(action="publish")
        self.append(action="external_send")
        first, second = self.entries()
        self.assertEqual(second["prev_hash"], first["entry_hash"])
        self.assertEqual(self.audit.verify(), [])

    def test_editing_an_entry_breaks_verification(self) -> None:
        self.append(actor="cmo-marketing")
        self.append(actor="cro-sales")
        lines = self.log.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[0])
        tampered["actor"] = "someone-else"
        lines[0] = json.dumps(tampered, separators=(",", ":"), sort_keys=True)
        self.log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertNotEqual(self.audit.verify(), [])

    def test_the_chain_anchors_lines_written_before_it_existed(self) -> None:
        """The seven hand-authored lines predate the chain; they must still be sealed."""
        legacy = {"ts": "2026-07-14T09:00:00Z", "actor": "coo-operations",
                  "action": "log.genesis", "category": "green", "target": "logs/audit-log.jsonl",
                  "approval": "not-required", "evidence": "logs/README.md",
                  "outcome": "ok", "note": "seeded by hand before the writer existed"}
        self.log.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
        self.append()
        self.assertEqual(self.audit.verify(), [])

        lines = self.log.read_text(encoding="utf-8").splitlines()
        legacy["note"] = "quietly rewritten"
        lines[0] = json.dumps(legacy)
        self.log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertNotEqual(self.audit.verify(), [])

    def test_an_unknown_category_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            self.append(category="purple")

    def test_an_unknown_outcome_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            self.append(outcome="probably-fine")

    def test_evidence_must_point_at_something_that_exists(self) -> None:
        with self.assertRaises(SystemExit):
            self.append(evidence="logs/does-not-exist.md")


class GateProducesAuditTest(unittest.TestCase):
    """The behaviour that matters: gates log themselves, with no agent involved."""

    def setUp(self) -> None:
        self._runs = tempfile.TemporaryDirectory()
        self.addCleanup(self._runs.cleanup)
        self._logdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._logdir.cleanup)
        self.log = Path(self._logdir.name) / "audit-log.jsonl"

        self._previous = {k: os.environ.get(k) for k in ("MYORG_RUNS_DIR", "MYORG_AUDIT_LOG")}
        os.environ["MYORG_RUNS_DIR"] = self._runs.name
        os.environ["MYORG_AUDIT_LOG"] = str(self.log)
        self.addCleanup(self._restore)

        from runtime import company_runtime
        self.core = importlib.reload(company_runtime)
        self.addCleanup(lambda: importlib.reload(company_runtime))

    def _restore(self) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def ns(self, **fields):
        import argparse
        return argparse.Namespace(**fields)

    def entries(self) -> list[dict]:
        if not self.log.is_file():
            return []
        return [json.loads(line) for line in
                self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def make_run(self, run_id: str, action: str) -> None:
        workflow = {"version": 1, "id": f"wf-{run_id}", "goal": f"probe {run_id}",
                    "max_cycles": 12,
                    "steps": [{"id": "s1", "owner": "cmo-marketing", "action": action,
                               "depends_on": [], "max_attempts": 2}]}
        path = Path(self._runs.name) / f"{run_id}.wf.json"
        path.write_text(json.dumps(workflow), encoding="utf-8")
        self.core.create_run(self.ns(workflow=str(path), run_id=run_id,
                                     actor="chief-of-staff",
                                     request_id=f"create-{run_id}", org="default"))

    def request(self, run_id: str) -> None:
        self.core.request_step(self.ns(run_id=run_id, step="s1", actor="cmo-marketing",
                                       request_id=f"req-{run_id}"))

    def test_parking_a_yellow_step_writes_an_audit_line(self) -> None:
        self.make_run("aud-yellow", "publish")
        self.request("aud-yellow")
        written = self.entries()
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0]["category"], "yellow")
        self.assertEqual(written[0]["approval"], "pending")
        self.assertEqual(written[0]["outcome"], "awaiting-approval")
        self.assertIn("aud-yellow", written[0]["target"])

    def test_a_red_step_is_recorded_as_refused(self) -> None:
        self.make_run("aud-red", "move_money")
        self.request("aud-red")
        refusals = [e for e in self.entries() if e["outcome"] == "refused"]
        self.assertEqual(len(refusals), 1)
        self.assertEqual(refusals[0]["category"], "red")

    def test_a_green_step_writes_no_audit_line(self) -> None:
        self.make_run("aud-green", "draft")
        self.request("aud-green")
        self.assertEqual(self.entries(), [])

    def test_approving_a_step_records_who_approved_it(self) -> None:
        self.make_run("aud-approve", "publish")
        self.request("aud-approve")
        self.core.approve(self.ns(run_id="aud-approve", step="s1", approver="Dipuraj",
                                  approval_ref="ticket-42", request_id="app-1"))
        granted = [e for e in self.entries() if e["approval"] == "granted"]
        self.assertEqual(len(granted), 1)
        self.assertEqual(granted[0]["actor"], "Dipuraj")
        self.assertIn("ticket-42", granted[0]["note"])

    def test_rejecting_a_step_is_recorded_as_denied(self) -> None:
        self.make_run("aud-reject", "publish")
        self.request("aud-reject")
        self.core.reject(self.ns(run_id="aud-reject", step="s1", approver="Dipuraj",
                                 approval_ref="ticket-43", request_id="rej-1"))
        denied = [e for e in self.entries() if e["approval"] == "denied"]
        self.assertEqual(len(denied), 1)

    def test_a_run_reaching_a_terminal_state_is_recorded(self) -> None:
        self.make_run("aud-terminal", "publish")
        self.request("aud-terminal")
        self.core.reject(self.ns(run_id="aud-terminal", step="s1", approver="Dipuraj",
                                 approval_ref="ticket-44", request_id="rej-2"))
        terminal = [e for e in self.entries() if e["action"] == "run.rejected"]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["outcome"], "blocked")

    def test_the_gate_does_not_happen_if_it_cannot_be_logged(self) -> None:
        """Fail closed: an unwritable audit log must stop the gated transition."""
        os.environ["MYORG_AUDIT_LOG"] = str(Path(self._logdir.name))  # a directory, not a file
        self.make_run("aud-closed", "publish")
        with self.assertRaises(SystemExit):
            self.request("aud-closed")
        state = self.core.read_events("aud-closed")[-1]
        self.assertEqual(state["steps"]["s1"]["status"], "ready")


if __name__ == "__main__":
    unittest.main()
