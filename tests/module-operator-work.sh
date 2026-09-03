#!/usr/bin/env bash
# MODULE suite — Work in (an operator's idea) and work out (what a run produced).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── operator work: a person is a trigger source; evidence is readable and fenced ──"
python3 -m unittest -v tests.test_operator_work
