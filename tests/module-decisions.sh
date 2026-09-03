#!/usr/bin/env bash
# MODULE suite — Human decision queue, run log to HTTP (HITL-04, API-02). Run by tests/run.sh.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── The decision queue, end to end ──"
python3 -m unittest -v tests.test_decisions
echo "── Memory decisions on the same surface (B-09) ──"
python3 -m unittest -v tests.test_memory_decisions
