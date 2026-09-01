#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Fail-closed release evidence gate ──"
python3 -m unittest -v tests.test_release_gate
