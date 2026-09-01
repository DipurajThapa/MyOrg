#!/usr/bin/env bash
# MODULE suite — What a run may spend, and what happens when it has (A-01, A-05, REC-11).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Cost recording, the run ceiling, and the budget extension ──"
python3 -m unittest -v tests.test_budget
