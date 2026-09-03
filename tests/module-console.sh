#!/usr/bin/env bash
# MODULE suite — The local operator console served by the runtime API.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── console: one page, loopback only, no authority the CLI does not already grant ──"
python3 -m unittest -v tests.test_console
