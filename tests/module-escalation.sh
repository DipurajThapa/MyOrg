#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
rc=0
echo "── escalation ──"
python3 -m unittest -v tests.test_escalation || rc=1
echo "── the GitHub operator inbox (NOTIFY-01) ──"
python3 -m unittest -v tests.test_notify_github || rc=1
exit $rc
