#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Production foundation security and recovery ──"
python3 -m unittest -v tests.test_production_foundation
echo "── Reproducible toolchain: the manifest matches what the code imports (DEP-06) ──"
python3 -m unittest -v tests.test_dependencies
