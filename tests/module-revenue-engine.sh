#!/usr/bin/env bash
# MODULE suite — Revenue Engine (Tier 0/1/2 revenue gaps): ar-collections, renewals-retention,
# deal-desk, funnel-attribution, kpi-tree, demand-gen + the customer-success and revops agents.
# Run by tests/run.sh. Remove any skill and its checks together; core.sh stays green without this file.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
pass=0; fail=0
check(){ if eval "$2"; then echo "  ✅ PASS  $1"; pass=$((pass+1)); else echo "  ❌ FAIL  $1"; fail=$((fail+1)); fi; }
# Windows Git-Bash grep cannot match 4-byte emoji (U+1F7E1 and friends), so a policy
# marker check would pass on Linux and fail here. Compare the text exactly instead.
contains(){ python3 -c "import sys,pathlib;sys.exit(0 if sys.argv[2] in pathlib.Path(sys.argv[1]).read_text(encoding='utf-8') else 1)" "$1" "$2"; }

echo "── V1 Skills present ──"
for s in ar-collections renewals-retention deal-desk funnel-attribution kpi-tree demand-gen; do
  check "$s SKILL.md" "[ -f .claude/skills/$s/SKILL.md ]"
done

echo ""; echo "── V2 New departments wired (agent file + indexed + routed) ──"
for a in customer-success revops; do
  check "$a agent exists"        "[ -f .claude/agents/$a.md ]"
  check "$a indexed in CLAUDE.md" "grep -q '\`$a\`' CLAUDE.md"
  check "$a section in routing-map" "grep -q \"agent: \\\`$a\\\`\" company/routing-map.md"
  check "$a has Charter + decision rights" "grep -q '## Charter' .claude/agents/$a.md && grep -qi 'Decision rights' .claude/agents/$a.md"
done

echo ""; echo "── V3 Boundaries explicit (no role overlap) ──"
check "CS owns proactive; support stays reactive"  "grep -qi 'reactive' .claude/agents/head-of-customer.md && grep -qi 'customer-success' .claude/agents/head-of-customer.md"
check "CS defers reactive to head-of-customer"     "grep -qi 'head-of-customer' .claude/agents/customer-success.md"
check "revops defers selling to CRO"               "grep -qi 'selling (CRO)' .claude/agents/revops.md"

echo ""; echo "── V4 Governance gates in every revenue skill ──"
check "ar-collections: body hard-rule gates every send" "grep -q 'Every send is drafted and approval-gated' .claude/skills/ar-collections/SKILL.md"
check "ar-collections: never move money" "grep -qi 'never move money' .claude/skills/ar-collections/SKILL.md"
check "renewals: sends/discounts gated"  "grep -qiE 'drafted, shown, approved' .claude/skills/renewals-retention/SKILL.md"
check "deal-desk: no send without human" "grep -qi 'without explicit human approval' .claude/skills/deal-desk/SKILL.md"
check "deal-desk: approval matrix"       "grep -qi 'discount' .claude/skills/deal-desk/SKILL.md && grep -qi 'Approval needed' .claude/skills/deal-desk/SKILL.md"
check "funnel: standing-rule changes gated" "grep -qi 'standing rule' .claude/skills/funnel-attribution/SKILL.md"
check "demand-gen: no spend without approval" "grep -qi 'No spend' .claude/skills/demand-gen/SKILL.md"
check "demand-gen: sends stay gated PER-SEND (no automation carve-out)" "contains .claude/skills/demand-gen/SKILL.md 'every send stays 🟡 per-send'"
check "demand-gen: auto-send fast-lane explicitly not built" "grep -qi 'not.*part of this skill' .claude/skills/demand-gen/SKILL.md"
check "kpi-tree: no fabricated inputs"   "grep -qi 'Never fabricate' .claude/skills/kpi-tree/SKILL.md"
check "kpi-tree: gated actions log via audit-log" "grep -q 'audit-log' .claude/skills/kpi-tree/SKILL.md"

echo ""; echo "── V5 Discipline sections (red flags + verification) in each skill ──"
miss=0
for s in ar-collections renewals-retention deal-desk funnel-attribution kpi-tree demand-gen; do
  grep -qi 'Red flags' .claude/skills/$s/SKILL.md || { echo "     · $s missing Red flags"; miss=$((miss+1)); }
  grep -qi 'Verification before claiming done' .claude/skills/$s/SKILL.md || { echo "     · $s missing Verification"; miss=$((miss+1)); }
done
check "all six carry Red flags + Verification" "[ $miss -eq 0 ]"

echo ""; echo "── V6 Outcome instrumentation & leak detection ──"
check "north-star tree section (body, not description)" "grep -q '## 1. The north-star tree' .claude/skills/kpi-tree/SKILL.md"
check "revenue-leak sweep section (body)"    "grep -q 'Revenue-leak sweep' .claude/skills/kpi-tree/SKILL.md"
check "experiments need decision rules"      "grep -q 'decision rule (' .claude/skills/kpi-tree/SKILL.md"
check "outcome-not-activity principle"       "grep -q 'not files produced' .claude/skills/kpi-tree/SKILL.md"

echo ""; echo "── V7 Cross-functional handoffs registered ──"
check "inbound-lead play in playbooks"   "grep -qi 'Inbound lead' company/playbooks.md"
check "dunning play in playbooks"        "grep -qi 'Overdue invoice' company/playbooks.md"
check "renewal play in playbooks"        "grep -qi 'Renewal window' company/playbooks.md"
check "after-hours rule (no unattended sends)" "grep -qi 'no unattended sends' company/playbooks.md"
check "gap ledger tracks dispositions"   "[ -f docs/GAP-LEDGER.md ] && grep -q 'BUILT' docs/GAP-LEDGER.md && grep -q 'BLOCKED-ON-HUMAN' docs/GAP-LEDGER.md && grep -q 'DEFERRED' docs/GAP-LEDGER.md"
check "churn play routed to customer-success" "grep -A1 'Customer churn risk' company/playbooks.md | grep -qi 'Customer Success leads'"
check "vendor play includes security review"  "grep -A1 'New vendor' company/playbooks.md | grep -qi 'security-grc'"

echo ""; echo "──── MODULE revenue-engine: $pass passed / $fail failed ────"
[ $fail -eq 0 ] || exit 1
