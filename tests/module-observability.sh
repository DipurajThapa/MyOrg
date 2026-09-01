#!/usr/bin/env bash
# MODULE suite — What the company tells you about itself unattended (OBS-08). Run by tests/run.sh.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Runtime gauges, the /metrics scrape, and the autonomy alert rules ──"
python3 -m unittest -v tests.test_runtime_metrics
