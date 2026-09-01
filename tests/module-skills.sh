#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
echo "── Skill registry ──"
python3 -m unittest -v tests.test_skills
echo ""
python3 runtime/skills.py --check >/dev/null && echo "  ✅ PASS  every claimed skill resolves" || echo "  ❌ FAIL  unresolved skills"
