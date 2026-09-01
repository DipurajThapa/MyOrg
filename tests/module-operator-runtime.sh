#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Identity-bound operator runtime and observability ──"
python3 -m unittest -v tests.test_operator_runtime
