#!/usr/bin/env bash
# MODULE suite — Acceptance grading fails closed (VAL-07). Run by tests/run.sh.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── A quality gate that cannot run must not report a pass ──"
python3 -m unittest -v tests.test_grading
