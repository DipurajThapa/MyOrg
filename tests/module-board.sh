#!/usr/bin/env bash
# MODULE suite — The work board: the console's other view, as the stages work passes through.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── board: every column a real status, every button a real route, no invented control ──"
python3 -m unittest -v tests.test_board
