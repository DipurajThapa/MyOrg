#!/usr/bin/env bash
# MODULE suite — What a run may spend, and what happens when it has (A-01, A-05, REC-11).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
# Each test command's result is kept, not just the last one's: a module that ran two
# suites returned only the second's status, so a real failure in the first was reported
# as a pass by the whole run.
rc=0
echo "── Cost recording, the run ceiling, and the budget extension ──"
python3 -m unittest -v tests.test_budget || rc=1
echo "── Every model call the ceiling should see (B-04) ──"
python3 -m unittest -v tests.test_spend_coverage || rc=1
exit $rc
