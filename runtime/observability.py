#!/usr/bin/env python3
"""What this process is willing to tell you about itself.

Two halves, and until OBS-08 only the first existed. `Metrics` counts HTTP traffic --
the part of the company that is *not* autonomous. `RuntimeGauges` reports the part that
is: how many runs are moving, how long the oldest person-shaped decision has been
waiting, how deep the trigger queue is, and how many calls to other people's systems
left and were never resolved.

A company that runs unattended and exports only web-server metrics is one where a stalled
queue, a runaway spend, or an approval nobody answered are all invisible until somebody
happens to look. These are the four numbers that make those visible.
"""
from __future__ import annotations

import threading
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """One JSON object per line; request headers and bodies are never inspected."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        try:
            payload = json.loads(message) if message.startswith("{") else {"event": "application.log", "message": message}
        except json.JSONDecodeError:
            payload = {"event": "application.log", "message": message}
        payload.update({"timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                        "level": record.levelname.lower(), "logger": record.name})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, int], int] = defaultdict(int)
        self._duration_seconds: dict[str, float] = defaultdict(float)
        self._duration_count: dict[str, int] = defaultdict(int)
        self._inflight = 0

    def begin(self) -> None:
        with self._lock:
            self._inflight += 1

    def observe(self, method: str, status: int, duration_seconds: float) -> None:
        with self._lock:
            self._inflight = max(0, self._inflight - 1)
            self._requests[(method, status)] += 1
            self._duration_seconds[method] += duration_seconds
            self._duration_count[method] += 1

    def render(self) -> bytes:
        with self._lock:
            requests = dict(self._requests)
            durations = dict(self._duration_seconds)
            duration_counts = dict(self._duration_count)
            inflight = self._inflight
        lines = [
            "# HELP myorg_http_requests_total Completed HTTP requests.",
            "# TYPE myorg_http_requests_total counter",
        ]
        for (method, status), count in sorted(requests.items()):
            lines.append(f'myorg_http_requests_total{{method="{method}",status="{status}"}} {count}')
        lines.extend((
            "# HELP myorg_http_request_duration_seconds_sum Cumulative request duration.",
            "# TYPE myorg_http_request_duration_seconds_sum counter",
        ))
        for method, value in sorted(durations.items()):
            lines.append(f'myorg_http_request_duration_seconds_sum{{method="{method}"}} {value:.6f}')
        lines.extend((
            "# HELP myorg_http_request_duration_seconds_count Requests included in duration totals.",
            "# TYPE myorg_http_request_duration_seconds_count counter",
        ))
        for method, value in sorted(duration_counts.items()):
            lines.append(f'myorg_http_request_duration_seconds_count{{method="{method}"}} {value}')
        lines.extend((
            "# HELP myorg_http_requests_inflight Requests currently executing.",
            "# TYPE myorg_http_requests_inflight gauge",
            f"myorg_http_requests_inflight {inflight}",
        ))
        return ("\n".join(lines) + "\n").encode("utf-8")


RUN_STATES = ("running", "waiting on you", "stalled", "finished", "failed")
SEVERITIES = ("blocking", "attention", "routine")
SNAPSHOT_TTL_SECONDS = 15.0
# "Nothing is authorized" is a real state, and not the same as "expiring now". A zero here
# would fire the expiry alert on every install that has no connectors.
NO_AUTHORIZATION_EXPIRY = 31_536_000.0  # a year away, i.e. nothing to worry about


def _age_seconds(stamp: str | None, now: datetime) -> float:
    """How long ago, in seconds, never negative and never an exception."""
    if not stamp:
        return 0.0
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (now - moment).total_seconds())


class RuntimeGauges:
    """The autonomous half, sampled on demand.

    Collecting means reading every run log, so the result is cached for one scrape
    interval -- a metrics endpoint that gets slower as the company gets busier is a
    metrics endpoint people turn off.

    Nothing here may raise. A missing runs directory, an absent store and a half-written
    log are all ordinary states, not failures. But a collector that failed *silently*
    would recreate the exact gap OBS-08 exists to close, so failures are counted and
    exported: `myorg_runtime_snapshot_ok 0` is itself an alertable signal.
    """

    def __init__(self, store=None, ttl_seconds: float = SNAPSHOT_TTL_SECONDS):
        self.store = store
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._cached: dict | None = None
        self._cached_at = 0.0
        self._errors = 0

    # --- collection ---------------------------------------------------------------

    def _runs(self, sample: dict, now: datetime) -> None:
        from runtime.health import all_health
        counts = dict.fromkeys(RUN_STATES, 0)
        for run in all_health(now):
            counts[run.state] = counts.get(run.state, 0) + 1
        sample["runs"] = counts

    def _approvals(self, sample: dict, now: datetime) -> None:
        from runtime import approvals
        pending = approvals.pending()
        sample["approvals_waiting"] = len(pending)
        sample["approval_wait_seconds_max"] = max(
            (_age_seconds(item.waiting_since, now) for item in pending), default=0.0)

    def _notices(self, sample: dict, now: datetime) -> None:
        from runtime import notify
        counts = dict.fromkeys(SEVERITIES, 0)
        for notice in notify.outstanding():
            counts[notice.severity] = counts.get(notice.severity, 0) + 1
        sample["notices"] = counts

    def _triggers(self, sample: dict, now: datetime) -> None:
        if self.store is None:
            return
        depth, oldest = self.store.trigger_queue_summary()
        sample["trigger_queue_depth"] = depth
        sample["trigger_queue_oldest_seconds"] = _age_seconds(oldest, now)

    def _receipts(self, sample: dict, now: datetime) -> None:
        if self.store is None:
            return
        count, oldest = self.store.in_flight_receipt_summary()
        sample["receipts_in_flight"] = count
        sample["receipt_unsettled_seconds_max"] = _age_seconds(oldest, now)

    def _authorizations(self, sample: dict, now: datetime) -> None:
        """Seconds until the first enabled connector loses its authorization.

        Negative once it has passed. `NO_AUTHORIZATION_EXPIRY` when nothing is authorized,
        which is a real state and not the same as "expiring now" -- a zero here would fire
        the alert on every install that has no connectors.
        """
        if self.store is None:
            return
        soonest = self.store.soonest_authorization_expiry()
        if soonest:
            moment = datetime.fromisoformat(soonest.replace("Z", "+00:00"))
            sample["authorization_expires_seconds"] = (moment - now).total_seconds()

    def collect(self, now: datetime | None = None) -> dict:
        """One sample. Each source is isolated: a broken one costs its own numbers, not
        the whole endpoint."""
        moment = now or datetime.now(timezone.utc)
        started = time.monotonic()
        sample: dict = {"runs": dict.fromkeys(RUN_STATES, 0),
                        "notices": dict.fromkeys(SEVERITIES, 0),
                        "approvals_waiting": 0, "approval_wait_seconds_max": 0.0,
                        "trigger_queue_depth": 0, "trigger_queue_oldest_seconds": 0.0,
                        "receipts_in_flight": 0, "receipt_unsettled_seconds_max": 0.0,
                        "authorization_expires_seconds": NO_AUTHORIZATION_EXPIRY, "ok": 1}
        for source in (self._runs, self._approvals, self._notices, self._triggers,
                       self._receipts, self._authorizations):
            try:
                source(sample, moment)
            except Exception:  # noqa: BLE001 - a scrape must never take the server down
                sample["ok"] = 0
                self._errors += 1
        sample["duration_seconds"] = time.monotonic() - started
        sample["errors_total"] = self._errors
        return sample

    def sample(self, now: datetime | None = None) -> dict:
        with self._lock:
            fresh = self._cached is not None and (time.monotonic() - self._cached_at) < self.ttl_seconds
            if fresh and now is None:
                return dict(self._cached)  # type: ignore[arg-type]
        collected = self.collect(now)
        with self._lock:
            self._cached, self._cached_at = collected, time.monotonic()
        return collected

    # --- rendering ----------------------------------------------------------------

    def render(self, now: datetime | None = None) -> bytes:
        sample = self.sample(now)
        lines = [
            "# HELP myorg_runs Runs by health state.",
            "# TYPE myorg_runs gauge",
        ]
        for state in RUN_STATES:
            lines.append(f'myorg_runs{{state="{state.replace(" ", "_")}"}} {sample["runs"].get(state, 0)}')
        lines.extend((
            "# HELP myorg_approvals_waiting Steps parked for a human decision.",
            "# TYPE myorg_approvals_waiting gauge",
            f'myorg_approvals_waiting {sample["approvals_waiting"]}',
            "# HELP myorg_approval_wait_seconds_max Age of the longest-waiting decision.",
            "# TYPE myorg_approval_wait_seconds_max gauge",
            f'myorg_approval_wait_seconds_max {sample["approval_wait_seconds_max"]:.1f}',
            "# HELP myorg_notices_outstanding Undelivered notices by severity.",
            "# TYPE myorg_notices_outstanding gauge",
        ))
        for severity in SEVERITIES:
            lines.append(f'myorg_notices_outstanding{{severity="{severity}"}} '
                         f'{sample["notices"].get(severity, 0)}')
        lines.extend((
            "# HELP myorg_trigger_queue_depth Triggered work waiting to become runs.",
            "# TYPE myorg_trigger_queue_depth gauge",
            f'myorg_trigger_queue_depth {sample["trigger_queue_depth"]}',
            "# HELP myorg_trigger_queue_oldest_seconds Age of the oldest queued trigger.",
            "# TYPE myorg_trigger_queue_oldest_seconds gauge",
            f'myorg_trigger_queue_oldest_seconds {sample["trigger_queue_oldest_seconds"]:.1f}',
            "# HELP myorg_connector_receipts_in_flight Outward calls that left and were never resolved.",
            "# TYPE myorg_connector_receipts_in_flight gauge",
            f'myorg_connector_receipts_in_flight {sample["receipts_in_flight"]}',
            "# HELP myorg_connector_receipt_unsettled_seconds_max Age of the oldest unresolved call.",
            "# TYPE myorg_connector_receipt_unsettled_seconds_max gauge",
            f'myorg_connector_receipt_unsettled_seconds_max {sample["receipt_unsettled_seconds_max"]:.1f}',
            "# HELP myorg_connector_authorization_expires_seconds Until the first enabled connector loses access; negative once it has.",
            "# TYPE myorg_connector_authorization_expires_seconds gauge",
            f'myorg_connector_authorization_expires_seconds {sample["authorization_expires_seconds"]:.0f}',
            "# HELP myorg_runtime_snapshot_ok 1 when every source answered, 0 when one did not.",
            "# TYPE myorg_runtime_snapshot_ok gauge",
            f'myorg_runtime_snapshot_ok {sample["ok"]}',
            "# HELP myorg_runtime_snapshot_errors_total Sources that failed since start.",
            "# TYPE myorg_runtime_snapshot_errors_total counter",
            f'myorg_runtime_snapshot_errors_total {sample["errors_total"]}',
            "# HELP myorg_runtime_snapshot_duration_seconds Time taken to collect this sample.",
            "# TYPE myorg_runtime_snapshot_duration_seconds gauge",
            f'myorg_runtime_snapshot_duration_seconds {sample["duration_seconds"]:.6f}',
        ))
        return ("\n".join(lines) + "\n").encode("utf-8")
