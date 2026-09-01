#!/usr/bin/env bash
# MODULE suite — Real outward calls and the unknown outcome (TOOL-03, TOOL-04). Run by tests/run.sh.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Live connector gateway: admission, settlement, no double-send ──"
python3 -m unittest -v tests.test_live_gateway
