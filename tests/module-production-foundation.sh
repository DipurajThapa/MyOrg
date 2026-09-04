#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
# Each test command's result is kept, not just the last one's: a module that ran two
# suites returned only the second's status, so a real failure in the first was reported
# as a pass by the whole run.
rc=0
echo "── Production foundation security and recovery ──"
python3 -m unittest -v tests.test_production_foundation || rc=1
echo "── Reproducible toolchain: the manifest matches what the code imports (DEP-06) ──"
python3 -m unittest -v tests.test_dependencies || rc=1
exit $rc
