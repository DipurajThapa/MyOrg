#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
# Each test command's result is kept, not just the last one's: a module that ran two
# suites returned only the second's status, so a real failure in the first was reported
# as a pass by the whole run.
rc=0
rc=0
echo "── escalation ──"
python3 -m unittest -v tests.test_escalation || rc=1 || rc=1
echo "── the GitHub operator inbox (NOTIFY-01) ──"
python3 -m unittest -v tests.test_notify_github || rc=1 || rc=1
echo "── the email sink: the delivery that actually reaches a person ──"
python3 -m unittest -v tests.test_notify_email || rc=1
exit $rc
exit $rc
