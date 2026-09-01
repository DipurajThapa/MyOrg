#!/usr/bin/env bash
# MODULE suite — Rotating the signing key without an outage (SEC-09). Run by tests/run.sh.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Key rotation: sign with the current key, accept the previous one ──"
python3 -m unittest -v tests.test_key_rotation
