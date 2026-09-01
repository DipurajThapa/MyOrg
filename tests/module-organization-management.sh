#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
pass=0; fail=0
check(){ if eval "$2"; then echo "  ✅ PASS  $1"; pass=$((pass+1)); else echo "  ❌ FAIL  $1"; fail=$((fail+1)); fi; }
echo "── O1 Organization state workflow ──"
# cygpath: Git Bash hands POSIX paths to native Windows Python, which reads /c/x as C:\c\x.
tmp=$(mktemp -d); tmp=$(cygpath -m "$tmp" 2>/dev/null || printf %s "$tmp")
trap 'rm -rf "$tmp"' EXIT; export MYORG_STATE_DIR="$tmp"
goal=$(python3 scripts/org_state.py create goal "Ship first product" --outcome "One verified user workflow")
python3 scripts/org_state.py update goal "$goal" active >/dev/null
check "task rejects unknown department owner" "! python3 scripts/org_state.py create task 'Bad owner' --goal '$goal' --owner invented-agent >/dev/null 2>&1"
task=$(python3 scripts/org_state.py create task "Run workflow" --goal "$goal" --owner cto-engineering)
python3 scripts/org_state.py update task "$task" in_progress >/dev/null
check "goal cannot close with open tasks" "! python3 scripts/org_state.py update goal '$goal' achieved >/dev/null 2>&1"
check "task cannot close without evidence" "! python3 scripts/org_state.py update task '$task' done >/dev/null 2>&1"
python3 scripts/org_state.py update task "$task" done --evidence "tests/run.sh passed" >/dev/null
python3 scripts/org_state.py update goal "$goal" achieved >/dev/null
decision=$(python3 scripts/org_state.py create decision "Release workflow")
check "decision cannot approve without human evidence" "! python3 scripts/org_state.py update decision '$decision' approved >/dev/null 2>&1"
python3 scripts/org_state.py update decision "$decision" approved --approval "Human approved in session" >/dev/null
check "state validates" "python3 scripts/org_state.py validate >/dev/null"
check "status shows verified task" "python3 scripts/org_state.py status | grep -q $'${task}\\tdone'"
check "status shows approved decision" "python3 scripts/org_state.py status | grep -q $'${decision}\\tapproved'"
echo ""; echo "──── MODULE organization-management: $pass passed / $fail failed ────"
[ $fail -eq 0 ] || exit 1
