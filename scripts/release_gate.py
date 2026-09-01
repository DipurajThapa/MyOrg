#!/usr/bin/env python3
"""Fail closed unless every live release check, evidence reference, and sign-off is present."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--expect", choices=["ready", "blocked"], required=True)
    args = parser.parse_args()
    record = json.loads(args.record.read_text(encoding="utf-8"))
    required = {"schema_version", "source_revision", "environment", "checks", "evidence", "signoffs", "decision"}
    if set(record) != required or record["schema_version"] != 1:
        raise SystemExit("invalid release-gate schema")
    checks = record["checks"]
    evidence = record["evidence"]
    signoffs = record["signoffs"]
    ready = (SHA_RE.fullmatch(str(record["source_revision"])) is not None
             and record["environment"] not in {"", "development", "local"}
             and bool(checks) and all(value is True for value in checks.values())
             and bool(evidence) and all(isinstance(value, str) and len(value.strip()) >= 8 for value in evidence.values())
             and bool(signoffs) and all(isinstance(value, str) and len(value.strip()) >= 3 for value in signoffs.values()))
    expected_decision = "ready" if ready else "blocked"
    if record["decision"] != expected_decision:
        raise SystemExit(f"release decision is inconsistent: expected {expected_decision}")
    if args.expect != expected_decision:
        raise SystemExit(f"release gate is {expected_decision}, not {args.expect}")
    print(json.dumps({"decision": expected_decision, "checks": len(checks), "evidence": len(evidence),
                      "signoffs": len(signoffs)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
