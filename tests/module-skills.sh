#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Skill registry ──"
rc=0
python3 -m unittest -v tests.test_skills || rc=1
echo ""
if python3 runtime/skills.py --check >/dev/null; then
  echo "  ✅ PASS  every claimed skill resolves"
else
  echo "  ❌ FAIL  unresolved skills"; rc=1
fi
# A module that swallows its own failures makes the suite say PASS over a red test -- it did,
# once (2026-09-03), and the tracker records it.
exit $rc
