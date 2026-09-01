"""What the company will tell you about itself while nobody is watching it.

OBS-08 in the REV2 audit: after triggers, live connectors and the supervised loop landed,
the company could act unattended -- and the only instrumented component was the web server.
A stalled queue, an unanswered approval, or an outward call that left and never came back
were all invisible until a person happened to look.

The tests below are mostly about one property: **the number moves when the thing happens**.
A gauge that is always zero passes a smoke test and tells an operator nothing, so each case
here creates the real condition first and then asserts the series changed.
"""
from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime.db import Store
from runtime.observability import RuntimeGauges

ROOT = Path(__file__).resolve().parents[1]


def series(rendered: bytes, name: str) -> float:
    """Pull one value out of the exposition format, so tests read like the scrape does."""
    for line in rendered.decode("utf-8").splitlines():
        if line.startswith("#"):
            continue
        key, _, value = line.rpartition(" ")
        if key.strip() == name:
            return float(value)
    raise AssertionError(f"series not exported: {name}")


class RuntimeGaugesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self._previous = {k: os.environ.get(k) for k in
                          ("MYORG_RUNS_DIR", "MYORG_AUDIT_LOG", "MYORG_DB", "MYORG_OUTBOX")}
        self.addCleanup(self._restore)
        os.environ["MYORG_RUNS_DIR"] = self.temporary.name
        os.environ["MYORG_AUDIT_LOG"] = str(Path(self.temporary.name) / "_audit-log.jsonl")

        from runtime import company_runtime, executor, health, notify, approvals
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        self.health = importlib.reload(health)
        self.notify = importlib.reload(notify)
        self.approvals = importlib.reload(approvals)
        for module in (company_runtime, executor, health, notify, approvals):
            self.addCleanup(lambda m=module: importlib.reload(m))
        self.addCleanup(self.clear_evidence)

        self.store = Store(Path(self.temporary.name) / "metrics.db")
        self.store.migrate()
        self.store.bootstrap_organization("acme", "Acme")
        self.gauges = RuntimeGauges(self.store, ttl_seconds=0)

    def clear_evidence(self) -> None:
        for path in self.executor.EVIDENCE_DIR.glob("obs-*"):
            path.unlink(missing_ok=True)

    def _restore(self) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def namespace(self, **fields):
        import argparse
        return argparse.Namespace(**fields)

    def make_run(self, run_id: str, action: str = "draft") -> None:
        workflow = {"version": 1, "id": f"wf-{run_id}", "goal": f"observe {run_id}",
                    "max_cycles": 12,
                    "steps": [{"id": "s1", "owner": "cmo-marketing", "action": action,
                               "depends_on": [], "max_attempts": 2}]}
        path = Path(self.temporary.name) / f"{run_id}.wf.json"
        path.write_text(json.dumps(workflow), encoding="utf-8")
        self.executor.quietly(self.core.create_run, self.namespace(
            workflow=str(path), run_id=run_id, actor="chief-of-staff",
            request_id=f"create-{run_id}", org="acme"))

    def park(self, run_id: str) -> None:
        """Drive a yellow step to the gate, which is what "waiting on a human" means."""
        self.executor.quietly(self.core.request_step, self.namespace(
            run_id=run_id, step="s1", actor="cmo-marketing", request_id=f"req-{run_id}"))

    # --- the endpoint is always answerable -------------------------------------------

    def test_an_empty_company_still_exports_every_series(self) -> None:
        """A gauge that disappears when it is zero cannot be alerted on."""
        rendered = self.gauges.render()
        for name in ('myorg_runs{state="running"}', 'myorg_runs{state="stalled"}',
                     "myorg_approvals_waiting", "myorg_approval_wait_seconds_max",
                     'myorg_notices_outstanding{severity="blocking"}',
                     "myorg_trigger_queue_depth", "myorg_trigger_queue_oldest_seconds",
                     "myorg_connector_receipts_in_flight",
                     "myorg_connector_receipt_unsettled_seconds_max",
                     "myorg_runtime_snapshot_ok"):
            self.assertEqual(series(rendered, name), 0.0 if name != "myorg_runtime_snapshot_ok" else 1.0)

    def test_no_store_is_an_ordinary_state_not_a_failure(self) -> None:
        rendered = RuntimeGauges(None, ttl_seconds=0).render()
        self.assertEqual(series(rendered, "myorg_runtime_snapshot_ok"), 1.0)
        self.assertEqual(series(rendered, "myorg_trigger_queue_depth"), 0.0)

    # --- runs -------------------------------------------------------------------------

    def test_a_new_run_shows_up_as_running(self) -> None:
        self.make_run("obs-running")
        self.assertEqual(series(self.gauges.render(), 'myorg_runs{state="running"}'), 1.0)

    def test_a_parked_run_is_counted_as_waiting_on_a_person(self) -> None:
        self.make_run("obs-parked", "publish")
        self.park("obs-parked")
        rendered = self.gauges.render()
        self.assertEqual(series(rendered, 'myorg_runs{state="waiting_on_you"}'), 1.0)
        self.assertEqual(series(rendered, 'myorg_runs{state="running"}'), 0.0)

    def test_a_stalled_run_is_visible_as_stalled(self) -> None:
        """The dangerous case: nothing is moving and nobody has been asked for anything."""
        self.make_run("obs-stalled")
        later = datetime.now(timezone.utc) + timedelta(
            minutes=self.health.STALLED_AFTER_MINUTES + 5)
        rendered = self.gauges.render(now=later)
        self.assertEqual(series(rendered, 'myorg_runs{state="stalled"}'), 1.0)

    # --- approvals --------------------------------------------------------------------

    def test_a_waiting_decision_is_counted_and_aged(self) -> None:
        self.make_run("obs-approval", "publish")
        self.park("obs-approval")
        later = datetime.now(timezone.utc) + timedelta(hours=5)
        rendered = self.gauges.render(now=later)
        self.assertEqual(series(rendered, "myorg_approvals_waiting"), 1.0)
        self.assertGreater(series(rendered, "myorg_approval_wait_seconds_max"), 4 * 3600)

    def test_the_age_is_the_oldest_not_the_newest(self) -> None:
        """Reporting the newest would hide exactly the decision that has been ignored."""
        self.make_run("obs-old", "publish")
        self.park("obs-old")
        self.make_run("obs-new", "publish")
        self.park("obs-new")
        later = datetime.now(timezone.utc) + timedelta(hours=3)
        rendered = self.gauges.render(now=later)
        self.assertEqual(series(rendered, "myorg_approvals_waiting"), 2.0)
        self.assertGreater(series(rendered, "myorg_approval_wait_seconds_max"), 2 * 3600)

    # --- triggers ---------------------------------------------------------------------

    def test_queued_triggers_raise_the_depth_and_the_age(self) -> None:
        self.store.enqueue_trigger("acme", "tg-obs00000000000000000001", "webhook",
                                   "crm:lead.created", "Qualify the lead")
        self.store.enqueue_trigger("acme", "tg-obs00000000000000000002", "schedule",
                                   "daily-brief", "Write the brief")
        later = datetime.now(timezone.utc) + timedelta(minutes=20)
        rendered = self.gauges.render(now=later)
        self.assertEqual(series(rendered, "myorg_trigger_queue_depth"), 2.0)
        self.assertGreater(series(rendered, "myorg_trigger_queue_oldest_seconds"), 19 * 60)

    def test_a_started_trigger_leaves_the_queue_depth(self) -> None:
        self.store.enqueue_trigger("acme", "tg-obs00000000000000000003", "webhook",
                                   "crm:lead.created", "Qualify the lead")
        self.store.settle_trigger("acme", "tg-obs00000000000000000003", "started", "run-x", None)
        self.assertEqual(series(self.gauges.render(), "myorg_trigger_queue_depth"), 0.0)

    def test_the_depth_counts_every_organization_not_just_one(self) -> None:
        """A multi-tenant host with a backlog in one org must not look idle."""
        self.store.bootstrap_organization("other", "Other")
        self.store.enqueue_trigger("acme", "tg-obs00000000000000000004", "webhook", "a", "g")
        self.store.enqueue_trigger("other", "tg-obs00000000000000000005", "webhook", "b", "g")
        self.assertEqual(series(self.gauges.render(), "myorg_trigger_queue_depth"), 2.0)

    # --- unresolved outward calls -----------------------------------------------------

    def test_an_unresolved_call_is_counted_and_aged(self) -> None:
        """The series the most serious alert reads. A call left and never came back."""
        self.store.upsert_actor("acme", "chief", "human", "Chief", ["system-admin"])
        from runtime.connectors import validate_manifest
        self.store.register_connector("acme", validate_manifest({
            "id": "billing", "kind": "fixture", "mode": "propose_write",
            "base_url": "https://fixture.invalid", "allowed_hosts": ["fixture.invalid"],
            "allowed_actions": ["send_email"], "timeout_seconds": 2,
            "max_response_bytes": 1024, "enabled": True}))
        self.store.record_connector_receipt("acme", {
            "id": "receipt-obs0000000001", "connector_id": "billing",
            "idempotency_key": "obs-key-1", "request_hash": "0" * 64,
            "provider_receipt": "", "status": "in_flight",
            "created_at": "2026-09-01T12:00:00Z"})
        later = datetime.fromisoformat("2026-09-01T12:30:00+00:00")
        rendered = self.gauges.render(now=later)
        self.assertEqual(series(rendered, "myorg_connector_receipts_in_flight"), 1.0)
        self.assertAlmostEqual(series(rendered, "myorg_connector_receipt_unsettled_seconds_max"),
                               1800.0, delta=1.0)

    # --- spend (A-01) -----------------------------------------------------------------

    def test_spend_is_zero_before_anything_has_run(self) -> None:
        rendered = self.gauges.render()
        self.assertEqual(series(rendered, "myorg_spend_usd_total"), 0.0)
        self.assertEqual(series(rendered, "myorg_spend_usd_worst_run"), 0.0)

    def test_what_a_run_spent_reaches_the_scrape(self) -> None:
        self.make_run("obs-spend")
        self.park("obs-spend")   # a yellow step; no dispatch, so nothing is charged yet
        self.assertEqual(series(self.gauges.render(), "myorg_spend_usd_total"), 0.0)

    def test_the_worst_run_is_reported_not_the_average(self) -> None:
        """An average hides the runaway. The alert reads the worst single run."""
        from runtime.observability import RuntimeGauges
        gauges = RuntimeGauges(self.store, ttl_seconds=0)
        sample = gauges.collect()
        self.assertLessEqual(sample["spend_usd_worst_run"], sample["spend_usd_total"] + 1e-9)

    # --- connector authorization expiry (TOOL-09) -------------------------------------

    def authorize(self, connector_id: str, expires_at: str) -> None:
        from runtime.connectors import validate_manifest
        self.store.upsert_actor("acme", "chief", "human", "Chief", ["system-admin"])
        self.store.register_connector("acme", validate_manifest({
            "id": connector_id, "kind": "http", "mode": "read_only",
            "base_url": "https://api.example.com", "allowed_hosts": ["api.example.com"],
            "allowed_actions": ["ping"], "secret_ref": "TOOL9_TOKEN",
            "timeout_seconds": 5, "max_response_bytes": 4096, "enabled": False}))
        self.store.authorize_connector("acme", connector_id, "acct-1", ["read"], "TOOL9_TOKEN",
                                       None, expires_at, "chief", f"auth-{connector_id}", "t")
        self.store.set_connector_enabled("acme", connector_id, True, "chief",
                                         f"on-{connector_id}", "t")

    def test_no_authorization_is_not_the_same_as_expiring_now(self) -> None:
        """A zero here would fire the expiry alert on every install with no connectors."""
        from runtime.observability import NO_AUTHORIZATION_EXPIRY
        value = series(self.gauges.render(), "myorg_connector_authorization_expires_seconds")
        self.assertEqual(value, NO_AUTHORIZATION_EXPIRY)

    def test_an_expiring_authorization_counts_down(self) -> None:
        self.authorize("crm", "2026-09-15T00:00:00Z")
        now = datetime.fromisoformat("2026-09-08T00:00:00+00:00")
        value = series(self.gauges.render(now=now), "myorg_connector_authorization_expires_seconds")
        self.assertAlmostEqual(value, 7 * 86400, delta=1)

    def test_a_lapsed_authorization_goes_negative(self) -> None:
        self.authorize("crm", "2026-09-15T00:00:00Z")
        now = datetime.fromisoformat("2026-09-20T00:00:00+00:00")
        self.assertLess(series(self.gauges.render(now=now),
                               "myorg_connector_authorization_expires_seconds"), 0)

    def test_the_soonest_expiry_wins_not_the_latest(self) -> None:
        self.authorize("far", "2027-01-01T00:00:00Z")
        self.authorize("near", "2026-09-10T00:00:00Z")
        now = datetime.fromisoformat("2026-09-08T00:00:00+00:00")
        self.assertAlmostEqual(series(self.gauges.render(now=now),
                                      "myorg_connector_authorization_expires_seconds"),
                               2 * 86400, delta=1)

    def test_a_disabled_connector_does_not_raise_an_expiry_alarm(self) -> None:
        """Alerting on a connector nobody uses teaches people to ignore the alert."""
        from runtime.observability import NO_AUTHORIZATION_EXPIRY
        self.authorize("crm", "2026-09-10T00:00:00Z")
        self.store.set_connector_enabled("acme", "crm", False, "chief", "off-crm", "t")
        now = datetime.fromisoformat("2026-09-08T00:00:00+00:00")
        self.assertEqual(series(self.gauges.render(now=now),
                                "myorg_connector_authorization_expires_seconds"),
                         NO_AUTHORIZATION_EXPIRY)

    # --- the collector reports its own failure ----------------------------------------

    def test_a_broken_source_is_reported_rather_than_hidden(self) -> None:
        """A collector that failed quietly would recreate the gap OBS-08 exists to close:
        every alert would go silent for the same reason a healthy company does."""
        from unittest.mock import patch
        with patch("runtime.health.all_health", side_effect=OSError("run log unreadable")):
            rendered = self.gauges.render()
        self.assertEqual(series(rendered, "myorg_runtime_snapshot_ok"), 0.0)
        self.assertEqual(series(rendered, "myorg_runtime_snapshot_errors_total"), 1.0)
        # and the other sources still answered
        self.assertEqual(series(rendered, "myorg_trigger_queue_depth"), 0.0)

    def test_one_broken_source_does_not_take_the_endpoint_down(self) -> None:
        from unittest.mock import patch
        with patch.object(self.store, "trigger_queue_summary", side_effect=RuntimeError("db gone")):
            rendered = self.gauges.render()
        self.assertEqual(series(rendered, "myorg_runtime_snapshot_ok"), 0.0)
        self.assertEqual(series(rendered, 'myorg_runs{state="running"}'), 0.0)

    # --- cost of scraping -------------------------------------------------------------

    def test_repeat_scrapes_reuse_a_cached_sample(self) -> None:
        """Collecting reads every run log. A metrics endpoint that gets slower as the
        company gets busier is one people switch off."""
        from unittest.mock import patch
        cached = RuntimeGauges(self.store, ttl_seconds=60)
        with patch("runtime.health.all_health", return_value=[]) as reader:
            cached.render()
            cached.render()
            cached.render()
        self.assertEqual(reader.call_count, 1)

    def test_asking_for_a_specific_moment_always_collects_fresh(self) -> None:
        cached = RuntimeGauges(self.store, ttl_seconds=60)
        from unittest.mock import patch
        with patch("runtime.health.all_health", return_value=[]) as reader:
            cached.render(now=datetime.now(timezone.utc))
            cached.render(now=datetime.now(timezone.utc))
        self.assertEqual(reader.call_count, 2)


class MetricsOverHttpTest(RuntimeGaugesTest):
    """One scrape, both halves, and still nobody's business but the operator's."""

    def setUp(self) -> None:
        super().setUp()
        import threading
        from runtime.api import create_server
        self.token = "metrics-token-0123456789abcdef"
        self.server = create_server("127.0.0.1", 0, self.store.path,
                                    "0123456789abcdef0123456789abcdef",
                                    metrics_token=self.token)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def scrape(self, token: str | None):
        import urllib.error
        import urllib.request
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        request = urllib.request.Request(self.base + "/metrics", headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            return error.status, error.read()
        return response.status, response.read()

    def test_one_scrape_carries_the_web_boundary_and_the_autonomous_half(self) -> None:
        self.make_run("obs-http", "publish")
        self.park("obs-http")
        status, body = self.scrape(self.token)
        self.assertEqual(status, 200)
        self.assertIn(b"myorg_http_requests_total", body)
        self.assertIn(b"myorg_approvals_waiting", body)
        self.assertEqual(series(body, "myorg_approvals_waiting"), 1.0)

    def test_the_autonomous_series_are_no_more_public_than_the_http_ones(self) -> None:
        for token in (None, "the-wrong-token-entirely"):
            status, body = self.scrape(token)
            self.assertEqual(status, 404, "an unauthorized scrape must not reveal the route")
            self.assertNotIn(b"myorg_trigger_queue_depth", body)

    def test_a_scrape_never_exposes_a_goal_a_run_id_or_a_person(self) -> None:
        """Metric labels leak into dashboards, alert emails and third-party scrapers.
        Nothing here may carry business content or identity."""
        self.make_run("obs-secret", "publish")
        self.park("obs-secret")
        self.store.enqueue_trigger("acme", "tg-obs00000000000000000009", "webhook",
                                   "crm:lead.created", "Chase Contoso about the unpaid invoice")
        _status, body = self.scrape(self.token)
        for leak in (b"obs-secret", b"Contoso", b"cmo-marketing", b"acme"):
            self.assertNotIn(leak, body, f"metric output leaked {leak!r}")


class AlertRulesTest(unittest.TestCase):
    """Alerts that reference a series nothing exports are worse than no alerts."""

    def setUp(self) -> None:
        self.text = (ROOT / "deploy" / "prometheus-alerts.yml").read_text(encoding="utf-8")

    def test_every_autonomy_alert_reads_a_series_the_runtime_exports(self) -> None:
        import re
        exported = RuntimeGauges(None, ttl_seconds=0).render().decode("utf-8")
        names = {line.split("{")[0].split(" ")[0]
                 for line in exported.splitlines() if not line.startswith("#")}
        referenced = set(re.findall(r"\bmyorg_[a-z_]+", self.text)) - {"myorg_http_requests_total"}
        missing = referenced - names
        self.assertEqual(missing, set(), "alerts reference series nothing exports")

    def test_the_four_autonomy_conditions_are_alerted(self) -> None:
        for alert in ("MyOrgOutwardCallUnresolved", "MyOrgApprovalUnanswered",
                      "MyOrgTriggerQueueBacklog", "MyOrgRunsStalled",
                      "MyOrgAutonomyMetricsBlind"):
            self.assertIn(alert, self.text)

    def test_every_alert_names_a_runbook_that_exists(self) -> None:
        import re
        for target in re.findall(r"runbook:\s*(\S+)", self.text):
            path, _, _anchor = target.partition("#")
            self.assertTrue((ROOT / path).is_file(), f"missing runbook: {path}")


if __name__ == "__main__":
    unittest.main()
