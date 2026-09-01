#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Agent execution API ──"
MYORG_AGENT_TOKEN="${MYORG_AGENT_TOKEN:-$(python3 -c "print('t'*40)")}" python3 -m unittest -v tests.test_agent_api
