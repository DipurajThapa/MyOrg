#!/usr/bin/env bash
# MODULE suite — Work that starts without a person (HOOK-02, HOOK-03, DEP-07). Run by tests/run.sh.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Signed webhooks, the clock, and the supervised loop ──"
python3 -m unittest -v tests.test_triggers
echo "── Who may decide what wakes the company up ──"
python3 -m unittest -v tests.test_trigger_admin
