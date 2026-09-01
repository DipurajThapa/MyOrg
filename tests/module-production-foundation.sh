#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Production foundation security and recovery ──"
python3 -m unittest -v tests.test_production_foundation
