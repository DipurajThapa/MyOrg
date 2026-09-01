#!/usr/bin/env bash
# MODULE suite — One step, one holder (REC-10). Run by tests/run.sh.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Step ownership is enforced by the state machine ──"
python3 -m unittest -v tests.test_ownership
