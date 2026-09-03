#!/usr/bin/env bash
# MODULE suite — A person can stop a run (B-02).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── cancel-run: the human stop, the race, and every terminal state handled ──"
python3 -m unittest -v tests.test_cancel
