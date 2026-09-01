#!/usr/bin/env python3
"""Low-cardinality in-process metrics for the HTTP boundary."""
from __future__ import annotations

import threading
import json
import logging
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
