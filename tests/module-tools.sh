#!/usr/bin/env bash
# MODULE suite — What a department may touch, and where (AGENT-06, EXEC-01). Run by tests/run.sh.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Scoped tool grants and per-step workspaces ──"
python3 -m unittest -v tests.test_tools
