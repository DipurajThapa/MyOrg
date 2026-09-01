#!/usr/bin/env bash
# Acceptance suite orchestrator for the Enterprise Company OS.
# Runs the business-agnostic CORE suite, then every optional MODULE suite present.
# Entry point is unchanged: `bash tests/run.sh`.  Add a module by dropping tests/module-<name>.sh.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

echo "════════ Enterprise · Acceptance Suite ════════"
rc=0

echo ""; echo "▓▓▓ CORE (business-agnostic) ▓▓▓"
bash tests/core.sh || rc=1

for m in tests/module-*.sh; do
  [ -e "$m" ] || continue
  echo ""; echo "▓▓▓ MODULE: $(basename "$m" .sh | sed 's/^module-//') ▓▓▓"
  bash "$m" || rc=1
done

echo ""
if [ $rc -eq 0 ]; then echo "════════ SUITE: PASS ════════"; else echo "════════ SUITE: FAIL ════════"; fi
exit $rc
