#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Run projection (log to store) ──"
python3 -m unittest -v tests.test_projection
