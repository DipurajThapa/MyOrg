#!/usr/bin/env bash
# MODULE suite — What an operator can find and flip without reading source (NOTIFY-01, B-03).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── notice delivery is discoverable; suspended means suspended ──"
python3 -m unittest -v tests.test_operator_surface
