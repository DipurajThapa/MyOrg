#!/usr/bin/env bash
# MODULE suite — Standing the company up on a real host (ARCH-06). Run by tests/run.sh.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Bootstrap, and the read model against real runs ──"
python3 -m unittest -v tests.test_bootstrap
