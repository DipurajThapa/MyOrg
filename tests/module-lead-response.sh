#!/usr/bin/env bash
# MODULE suite — Lead Response SLA (inbound lead → qualify → route → gated draft). Run by tests/run.sh.
# DEPENDS ON the audit-log module (R7 verifies the worked lead's lifecycle in logs/audit-log.jsonl).
# If you remove this module (.claude/skills/lead-response/ + examples/revenue-ops/), delete this
# file too — core.sh stays green without it.
# NOTE: SLA targets/ICP in config/sla-policy.md are TUNABLE — these tests check structure and
# invariants, not the shipped values, so tuning the policy does not break the suite.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
SKILL=".claude/skills/lead-response"
RUN="examples/revenue-ops/runs/sample-inbound-lead"
LOG="logs/audit-log.jsonl"
LEAD="lead-2026-07-14-001"
pass=0; fail=0
check(){ if eval "$2"; then echo "  ✅ PASS  $1"; pass=$((pass+1)); else echo "  ❌ FAIL  $1"; fail=$((fail+1)); fi; }

echo "── R1 Capability files ──"
check "SKILL.md"                 "[ -f $SKILL/SKILL.md ]"
check "active sla-policy"        "[ -f $SKILL/config/sla-policy.md ]"
check "policy TEMPLATE"          "[ -f $SKILL/config/sla-policy.TEMPLATE.md ]"
check "policy EXAMPLE"           "[ -f $SKILL/config/sla-policy.EXAMPLE.md ]"
check "qualification rubric"     "[ -f $SKILL/references/qualification-rubric.md ]"
check "response templates"       "[ -f $SKILL/references/response-templates.md ]"

echo ""; echo "── R2 Mode-switch fixtures intact (active policy's mode is the user's choice) ──"
det(){ grep -q '<UNSET' "$1" && echo GENERAL || echo DEDICATED; }
check "TEMPLATE stays general (<UNSET> placeholders)" "[ \$(det $SKILL/config/sla-policy.TEMPLATE.md) = GENERAL ]"
check "EXAMPLE stays dedicated (fully filled)"        "[ \$(det $SKILL/config/sla-policy.EXAMPLE.md) = DEDICATED ]"
echo "     · active policy mode: $(det $SKILL/config/sla-policy.md) (informational — both modes valid)"

echo ""; echo "── R3 SLA policy structure (value-agnostic — tune freely) ──"
check "HOT band has a time target"   "grep -E '\*\*HOT\*\*' $SKILL/config/sla-policy.md | grep -qiE 'minute|hour|day|UNSET'"
check "WARM band has a time target"  "grep -E '\*\*WARM\*\*' $SKILL/config/sla-policy.md | grep -qiE 'minute|hour|day|UNSET'"
check "COLD band has a time target"  "grep -E '\*\*COLD\*\*' $SKILL/config/sla-policy.md | grep -qiE 'minute|hour|day|UNSET'"
check "business-hours timezone stated" "grep -A2 'Business hours' $SKILL/config/sla-policy.md | grep -qiE 'UTC|GMT|[A-Z][a-z]+/[A-Z][a-z]+|Eastern|Pacific|Central|UNSET'"
check "ICP definition anchored"      "grep -qi 'ICP definition' $SKILL/config/sla-policy.md"
check "breach handling defined"      "grep -qi 'breach' $SKILL/config/sla-policy.md"
check "breach never bypasses gate"   "grep -qi 'Never respond without approval' $SKILL/config/sla-policy.md"
check "sending gated per principles" "grep -q 'operating-principles.md' $SKILL/config/sla-policy.md"

echo ""; echo "── R4 Qualification rubric ──"
check "three dimensions"         "grep -q 'ICP fit' $SKILL/references/qualification-rubric.md && grep -qi 'Intent' $SKILL/references/qualification-rubric.md && grep -qi 'Completeness' $SKILL/references/qualification-rubric.md"
check "HOT/WARM/COLD bands"      "grep -q 'HOT' $SKILL/references/qualification-rubric.md && grep -q 'WARM' $SKILL/references/qualification-rubric.md && grep -q 'COLD' $SKILL/references/qualification-rubric.md"
check "anti-gaming rules"        "grep -qi 'anti-gaming' $SKILL/references/qualification-rubric.md"

echo ""; echo "── R5 Governance in the skill ──"
check "never auto-send"          "grep -qi 'Never auto-send' $SKILL/SKILL.md"
check "content-is-data rule"     "grep -qi 'data, not instructions' $SKILL/SKILL.md"
check "logs every step"          "grep -q 'audit-log' $SKILL/SKILL.md"
check "templates marked DRAFT"   "grep -qi 'DRAFT' $SKILL/references/response-templates.md"
check "no-fabrication rule"      "grep -qiE 'never invent|fabricat' $SKILL/references/response-templates.md"

echo ""; echo "── R6 Worked run (proves the manual workflow) ──"
check "00-intake"                "[ -f $RUN/00-intake.md ]"
check "01-qualification"         "[ -f $RUN/01-qualification.md ]"
check "02-acknowledgment-DRAFT"  "[ -f $RUN/02-acknowledgment-DRAFT.md ]"
check "03-task-contract"         "[ -f $RUN/03-task-contract-to-cro.md ]"
check "INDEX manifest"           "[ -f $RUN/INDEX.md ]"
check "draft gated on approval"  "grep -qi 'requires your explicit approval' $RUN/02-acknowledgment-DRAFT.md"
check "decision = appended new line (not edit)" "grep -qi 'append' $RUN/02-acknowledgment-DRAFT.md"
check "email body keeps placeholder slots (no fabricated availability)" "grep -q 'I have {{slot_option_1}} or {{slot_option_2}}' $RUN/02-acknowledgment-DRAFT.md"
check "task contract has authority + escalation" "grep -qi 'Decision authority' $RUN/03-task-contract-to-cro.md && grep -qi 'Escalation condition' $RUN/03-task-contract-to-cro.md"
check "fictional data only (.example)" "grep -q '@acme-robotics.example' $RUN/00-intake.md"

echo ""; echo "── R7 Audit lifecycle — scoped to $LEAD, SLA computed not claimed ──"
check "≥5 lifecycle entries for THIS lead" "[ \$(grep -c '\"target\":\"$LEAD\"' $LOG) -ge 5 ]"
check "intake logged (this lead)"        "grep '\"target\":\"$LEAD\"' $LOG | grep -q '\"action\":\"lead.intake\"'"
check "qualification logged (this lead)" "grep '\"target\":\"$LEAD\"' $LOG | grep -q '\"action\":\"lead.qualified\"'"
check "routing logged (this lead)"       "grep '\"target\":\"$LEAD\"' $LOG | grep -q '\"action\":\"lead.routed\"'"
check "draft logged (this lead)"         "grep '\"target\":\"$LEAD\"' $LOG | grep -q '\"action\":\"lead.response.drafted\"'"
check "LATEST email.send for this lead is pending" "grep '\"target\":\"$LEAD\"' $LOG | grep '\"action\":\"email.send\"' | tail -1 | grep -q '\"approval\":\"pending\"'"
check "no ungated send exists for this lead"       "! grep '\"target\":\"$LEAD\"' $LOG | grep '\"action\":\"email.send\"' | grep -q '\"approval\":\"not-required\"'"
if command -v python3 >/dev/null; then
  python3 - <<PY
import json,sys
from datetime import datetime
lead="$LEAD"; ts={}
for l in open("logs/audit-log.jsonl"):
    d=json.loads(l)
    if d["target"]==lead: ts[d["action"]]=d["ts"]
try:
    t0=datetime.fromisoformat(ts["lead.intake"].replace("Z","+00:00"))
    t1=datetime.fromisoformat(ts["lead.response.drafted"].replace("Z","+00:00"))
    mins=(t1-t0).total_seconds()/60
    ok = 0 < mins <= 15
    print(f"  {'✅ PASS' if ok else '❌ FAIL'}  SLA COMPUTED from log: intake→draft = {mins:.1f}m (must be ≤15m for HOT)")
    sys.exit(0 if ok else 1)
except KeyError as e:
    print(f"  ❌ FAIL  SLA computation impossible — missing entry {e}"); sys.exit(1)
PY
  [ $? -eq 0 ] && pass=$((pass+1)) || fail=$((fail+1))
else
  echo "  ⚠️  python3 missing — SLA computation SKIPPED"; fail=$((fail+1))
fi

echo ""; echo "── R8 Wired into the org ──"
check "cro-sales routes the skill" "grep -q 'lead-response' .claude/agents/cro-sales.md"
check "registered in routing-map"  "grep -q 'lead-response' company/routing-map.md"

echo ""; echo "──── MODULE lead-response: $pass passed / $fail failed ────"
[ $fail -eq 0 ] || exit 1
