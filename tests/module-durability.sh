#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── durability ──"
python3 -m unittest -v tests.test_durability
