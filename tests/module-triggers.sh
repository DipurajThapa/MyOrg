#!/usr/bin/env bash
# MODULE suite — Work that starts without a person (HOOK-02, HOOK-03, DEP-07). Run by tests/run.sh.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
# Each test command's result is kept, not just the last one's: a module that ran two
# suites returned only the second's status, so a real failure in the first was reported
# as a pass by the whole run.
rc=0
echo "── Signed webhooks, the clock, and the supervised loop ──"
python3 -m unittest -v tests.test_triggers || rc=1
echo "── Who may decide what wakes the company up ──"
python3 -m unittest -v tests.test_trigger_admin || rc=1
exit $rc
