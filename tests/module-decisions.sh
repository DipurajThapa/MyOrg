#!/usr/bin/env bash
# MODULE suite — Human decision queue, run log to HTTP (HITL-04, API-02). Run by tests/run.sh.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
# Each test command's result is kept, not just the last one's: a module that ran two
# suites returned only the second's status, so a real failure in the first was reported
# as a pass by the whole run.
rc=0
echo "── The decision queue, end to end ──"
python3 -m unittest -v tests.test_decisions || rc=1
echo "── Memory decisions on the same surface (B-09) ──"
python3 -m unittest -v tests.test_memory_decisions || rc=1
exit $rc
