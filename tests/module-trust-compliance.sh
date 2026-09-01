#!/usr/bin/env bash
# MODULE suite — Trust & Compliance (Tier 1 trust gaps + OS/structural policies):
# grc-readiness, privacy-program, contract-lifecycle, reputation-management + security-grc agent
# + secrets/degraded-mode/data-classification policy sections. Run by tests/run.sh.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
pass=0; fail=0
check(){ if eval "$2"; then echo "  ✅ PASS  $1"; pass=$((pass+1)); else echo "  ❌ FAIL  $1"; fail=$((fail+1)); fi; }

echo "── T1 Skills present ──"
for s in grc-readiness privacy-program contract-lifecycle reputation-management; do
  check "$s SKILL.md" "[ -f .claude/skills/$s/SKILL.md ]"
done

echo ""; echo "── T2 Security & GRC department wired ──"
check "security-grc agent exists"       "[ -f .claude/agents/security-grc.md ]"
check "indexed in CLAUDE.md"            "grep -q '\`security-grc\`' CLAUDE.md"
check "section in routing-map"          "grep -q 'agent: \`security-grc\`' company/routing-map.md"
check "Charter + decision rights"       "grep -q '## Charter' .claude/agents/security-grc.md && grep -qi 'Decision rights' .claude/agents/security-grc.md"
check "access changes stay 🔴 (human executes)" "grep -qi 'human executes' .claude/agents/security-grc.md"

echo ""; echo "── T3 GRC discipline ──"
check "no control claimed without evidence" "grep -qi 'Never claim a control' .claude/skills/grc-readiness/SKILL.md"
check "questionnaire sends gated"           "grep -qi 'questionnaire.send' .claude/skills/grc-readiness/SKILL.md"
check "honest-gap answers required"         "grep -qi 'Not currently implemented' .claude/skills/grc-readiness/SKILL.md"
check "questionnaires are data not instructions" "grep -qi 'data, not instructions' .claude/skills/grc-readiness/SKILL.md"

echo ""; echo "── T4 Privacy discipline ──"
check "DSR statutory clocks stated"      "grep -qi 'GDPR: 1 month' .claude/skills/privacy-program/SKILL.md"
check "breach runbook section exists"    "grep -q 'Breach-notification runbook' .claude/skills/privacy-program/SKILL.md"
check "72h clock runs from awareness"    "grep -q '72-hour clock runs from' .claude/skills/privacy-program/SKILL.md"
check "DSR response send is gated"       "grep -q 'sending is 🟡' .claude/skills/privacy-program/SKILL.md"
check "breach notifications are gated"   "grep -q 'sends are 🟡, human-approved' .claude/skills/privacy-program/SKILL.md"
check "hard-deletes stay 🔴 (human executes)" "grep -qi 'human executes\|the human execute' .claude/skills/privacy-program/SKILL.md"
check "identity verification before disclosure" "grep -qi 'Identity verification before disclosure' .claude/skills/privacy-program/SKILL.md"
check "not legal counsel disclaimer"     "grep -qi 'not legal counsel\|does not opine' .claude/skills/privacy-program/SKILL.md"

echo ""; echo "── T5 Contract-lifecycle discipline ──"
check "auto-renew trap detection"        "grep -qi 'auto-renew trap' .claude/skills/contract-lifecycle/SKILL.md"
check "renewal decisions gated"          "grep -qi 'human decision' .claude/skills/contract-lifecycle/SKILL.md"
check "obligations quoted from text"     "grep -qi 'quote the clause' .claude/skills/contract-lifecycle/SKILL.md"

echo ""; echo "── T6 Reputation discipline ──"
check "public replies gated"             "grep -qi 'No public reply' .claude/skills/reputation-management/SKILL.md"
check "crisis severity levels"           "grep -q 'C1' .claude/skills/reputation-management/SKILL.md && grep -q 'C3' .claude/skills/reputation-management/SKILL.md"
check "never fabricate reviews/testimonials" "grep -qi 'Never fabricate' .claude/skills/reputation-management/SKILL.md"
check "written consent gate in body (not just description)" "grep -q 'consent before any public use' .claude/skills/reputation-management/SKILL.md"

echo ""; echo "── T7 OS/structural policies documented ──"
check "secrets rules in connectors.md"   "grep -qi 'Secrets & credentials' company/connectors.md"
check "secrets never in stores/logs"     "grep -qi 'Secrets never enter' company/connectors.md"
check "degraded mode documented"         "grep -qi 'Degraded mode' company/connectors.md"
check "fall back, don't guess"           "grep -qi \"don't guess\" company/connectors.md"
check "revenue-critical connector order" "grep -qi 'revenue-critical first' company/connectors.md"
check "data classification table"        "grep -qi 'Data classification' company/memory-and-learning.md"
check "restricted class: PII in no store, ever" "grep -q 'no store, ever' company/memory-and-learning.md"

echo ""; echo "── T8 Discipline sections in each skill ──"
miss=0
for s in grc-readiness privacy-program contract-lifecycle reputation-management; do
  grep -qi 'Red flags' .claude/skills/$s/SKILL.md || { echo "     · $s missing Red flags"; miss=$((miss+1)); }
  grep -qi 'Verification before claiming done' .claude/skills/$s/SKILL.md || { echo "     · $s missing Verification"; miss=$((miss+1)); }
done
check "all four carry Red flags + Verification" "[ $miss -eq 0 ]"

echo ""; echo "──── MODULE trust-compliance: $pass passed / $fail failed ────"
[ $fail -eq 0 ] || exit 1
