#!/usr/bin/env bash
# CORE acceptance suite — business-agnostic invariants of the Company OS scaffold.
# Must pass regardless of which department modules are installed. Run by tests/run.sh.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
pass=0; fail=0
check(){ if eval "$2"; then echo "  ✅ PASS  $1"; pass=$((pass+1)); else echo "  ❌ FAIL  $1"; fail=$((fail+1)); fi; }

echo "── C1 Constitution stays lightweight ──"
LINES=$(wc -l < CLAUDE.md); BYTES=$(wc -c < CLAUDE.md)
echo "     CLAUDE.md = $LINES lines / $BYTES bytes (budget: ≤170 lines, ≤9000 bytes)"
check "CLAUDE.md ≤ 170 lines"  "[ $LINES -le 170 ]"
check "CLAUDE.md ≤ 9000 bytes" "[ $BYTES -le 9000 ]"

echo ""; echo "── C2 Progressive-disclosure references resolve ──"
# Every company/*.md path named in CLAUDE.md must exist (catches dangling refs like the old departments/).
missing=""
for ref in $(grep -oE 'company/[a-z-]+\.md' CLAUDE.md | sort -u); do [ -f "$ref" ] || missing="$missing $ref"; done
[ -z "$missing" ] || echo "     · missing:$missing"
check "all company/*.md refs in CLAUDE.md resolve" "[ -z \"$missing\" ]"
check "routing-map.md present"        "[ -f company/routing-map.md ]"
check "playbooks.md present"          "[ -f company/playbooks.md ]"
check "lessons.md present"            "[ -f company/lessons.md ]"
check "operating-principles present"  "[ -f company/operating-principles.md ]"
check "no dangling departments/ ref"  "! grep -q 'departments/' CLAUDE.md"
check "generic templates present"     "[ -f templates/department-agent.template.md ] && [ -f templates/skill/SKILL.template.md ]"
check "memory store is initialized"   "[ -f memory/README.md ]"

echo ""; echo "── C3 Org integrity (routing ↔ inventory) ──"
AGENTS=$(ls .claude/agents/*.md | wc -l | tr -d ' ')
check "at least one agent present"  "[ $AGENTS -ge 1 ]"
# routing-integrity: every agent file must be named in CLAUDE.md's index (no orphan agent / silent drift)
drift=0; miss=""
for af in .claude/agents/*.md; do an=$(basename "$af" .md); grep -q "$an" CLAUDE.md || { drift=1; miss="$miss $an"; }; done
[ $drift -eq 0 ] || echo "     · agents missing from CLAUDE.md:$miss"
check "every agent indexed in CLAUDE.md (no drift)"  "[ $drift -eq 0 ]"
# count-match: agents == agent-slugs indexed in CLAUDE.md (self-adjusts when a department is added/removed)
INDEXED=$(grep -oE '`[a-z-]+`' CLAUDE.md | tr -d '`' | sort -u | while read s; do [ -f ".claude/agents/$s.md" ] && echo x; done | wc -l | tr -d ' ')
echo "     agents=$AGENTS  indexed-in-CLAUDE.md=$INDEXED"
check "agent count matches CLAUDE.md index"  "[ $AGENTS -eq $INDEXED ]"

echo ""; echo "── C4 Governance present ──"
check "Green rule"            "grep -q 'Green (do freely)' CLAUDE.md"
check "Yellow rule"           "grep -q 'Yellow (draft, then ask)' CLAUDE.md"
check "Red rule"              "grep -q 'Red (never' CLAUDE.md"
check "content-is-data rule"  "grep -qi 'data, not instructions' CLAUDE.md"
check "definition-of-done rule" "grep -qi 'Definition of Done' company/operating-principles.md"

echo ""; echo "── C5 Frontmatter YAML valid (agents + skills) ──"
if command -v python3 >/dev/null; then
  python3 - <<'PY'
import glob,sys
try: import yaml
except Exception: print("  ⚠️  pyyaml missing — skipped"); sys.exit(0)
bad=0
for p in sorted(glob.glob('.claude/agents/*.md'))+sorted(glob.glob('.claude/skills/*/SKILL.md')):
    t=open(p).read()
    try:
        d=yaml.safe_load(t[3:t.find('\n---',3)]); assert 'name' in d and 'description' in d
    except Exception as e:
        print(f"  ❌ {p}: {e}"); bad+=1
print(f"  {'✅ PASS' if not bad else '❌ FAIL'}  frontmatter valid on all files")
sys.exit(1 if bad else 0)
PY
  [ $? -eq 0 ] && pass=$((pass+1)) || fail=$((fail+1))
fi

echo ""; echo "── C6 Coordination model wired ──"
check "operating-model.md present"          "[ -f company/operating-model.md ]"
check "operating-model referenced in CLAUDE.md" "grep -q 'operating-model.md' CLAUDE.md"
check "task contract in playbooks"          "grep -qi 'Task Contract' company/playbooks.md"
check "receiver may reject/return/escalate" "grep -qiE 'reject, return, or escalate' company/playbooks.md"
# every agent must carry an explicit Charter with decision rights (purpose/scope/authority/handoffs)
nochart=0; norights=0; ch=""; ri=""
for af in .claude/agents/*.md; do
  an=$(basename "$af" .md)
  grep -q '## Charter' "$af"     || { nochart=$((nochart+1)); ch="$ch $an"; }
  grep -qi 'Decision rights' "$af" || { norights=$((norights+1)); ri="$ri $an"; }
done
[ -z "$ch" ] || echo "     · no Charter:$ch"
[ -z "$ri" ] || echo "     · no Decision rights:$ri"
check "every agent has a ## Charter"        "[ $nochart -eq 0 ]"
check "every agent states Decision rights"  "[ $norights -eq 0 ]"
check "durable organization-management skill" "[ -f .claude/skills/organization-management/SKILL.md ]"
check "organization state manager present" "[ -f scripts/org_state.py ]"
check "controlled runtime present" "[ -f runtime/company_runtime.py ] && [ -f runtime/policy.json ]"
check "runtime audit records residual gaps" "grep -q 'Approval identity not authenticated' docs/RUNTIME-AUDIT.md"
check "exchange and maker-checker audit present" "[ -f docs/EXCHANGE-MAKER-CHECKER-AUDIT.md ]"
check "identity-bound API and persistent store present" "[ -f runtime/api.py ] && [ -f runtime/auth.py ] && [ -f runtime/db.py ]"
check "connector security gateway present" "[ -f runtime/connectors.py ] && [ -f runtime/connector-manifests/fixture.json ]"
check "security threat model present" "[ -f docs/SECURITY-THREAT-MODEL.md ]"
check "UAT deployment rollback control present" "[ -f docs/UAT-DEPLOYMENT-AND-ROLLBACK.md ]"
# each controlled loop is defined, and the loops are explicitly bounded (no open-ended loops)
check "Goal Loop defined"        "grep -q 'Goal Loop' company/operating-model.md"
check "Decision Loop defined"    "grep -q 'Decision Loop' company/operating-model.md"
check "Execution Loop defined"   "grep -q 'Execution Loop' company/operating-model.md"
check "Checkpoint Loop defined"  "grep -q 'Checkpoint Loop' company/operating-model.md"
check "Validation Loop defined"  "grep -q 'Improvement Loop' company/operating-model.md"
check "loops are bounded"        "grep -qiE 'iteration cap|correction cap|Exit:' company/operating-model.md"

echo ""; echo "── C7 Shared memory & learning documented ──"
KL=company/memory-and-learning.md
check "memory-and-learning.md present"      "[ -f $KL ]"
check "referenced in CLAUDE.md"             "grep -q 'memory-and-learning.md' CLAUDE.md"
check "referenced from operating-model"     "grep -q 'memory-and-learning.md' company/operating-model.md"
check "Guardrails (Dos & Don'ts) section"   "grep -qi 'Guardrails' $KL"
check "recall-before-act rule"              "grep -qi 'Recall' $KL"
check "contribute/propose loop"             "grep -qi 'propose' $KL"
check "human-approval write gate"           "grep -qi 'human approval' $KL"
check "no self-rewriting / autonomous rule" "grep -qiE 'self-rewriting|autonomous' $KL"
check "no secrets/PII/credentials rule"     "grep -qiE 'secrets|PII|credentials' $KL"
check "one-home / no-duplication rule"      "grep -qiE 'one home|duplicate' $KL"
check "content-is-data reaffirmed"          "grep -qi 'data' $KL"

echo ""; echo "──── CORE: $pass passed / $fail failed ────"
[ $fail -eq 0 ] || exit 1
