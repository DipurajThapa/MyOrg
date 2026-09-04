#!/usr/bin/env bash
# MODULE suite — Audit Log (append-only accountability record). Run by tests/run.sh.
# REMOVAL CHECKLIST — if you remove this module, do ALL of: delete logs/ and
# .claude/skills/audit-log/, delete this file, remove §8 from company/operating-principles.md,
# remove the audit-log-oversight line from .claude/agents/coo-operations.md, remove the
# "Audit log" section from company/routing-map.md, and remove/adapt module-lead-response.sh
# (its R7 depends on this module). core.sh stays green without any of it.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
LOG="logs/audit-log.jsonl"
SKILL=".claude/skills/audit-log/SKILL.md"
pass=0; fail=0
check(){ if eval "$2"; then echo "  ✅ PASS  $1"; pass=$((pass+1)); else echo "  ❌ FAIL  $1"; fail=$((fail+1)); fi; }

echo "── L1 Store present (self-seeded — independent of other modules) ──"
check "audit-log.jsonl exists"    "[ -f $LOG ]"
check "log is non-empty"          "[ -s $LOG ]"
check "module-own genesis entry"  "grep -q '\"action\":\"log.genesis\"' $LOG"
check "logs/README.md exists"     "[ -f logs/README.md ]"

echo ""; echo "── L2 Every line schema-valid; enums, ts format, chronology, evidence paths ──"
if command -v python3 >/dev/null; then
  python3 - <<'PY'
import json,sys,re,os
bad=0
FIELDS=('ts','actor','action','category','target','approval','evidence','outcome','note')
CATS={'green','yellow','red'}
APPR={'not-required','pending','granted','denied'}
OUT={'ok','awaiting-approval','blocked','breach-flagged','refused'}
TS=re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
prev=''
for i,l in enumerate(open('logs/audit-log.jsonl')):
    l=l.strip()
    if not l: continue
    try:
        d=json.loads(l)
        missing=[k for k in FIELDS if k not in d]
        assert not missing, f"missing fields: {missing}"
        assert d['category'] in CATS, f"bad category: {d['category']}"
        assert d['approval'] in APPR, f"bad approval: {d['approval']}"
        assert d['outcome'] in OUT, f"bad outcome: {d['outcome']}"
        assert TS.match(d['ts']), f"bad ts format: {d['ts']}"
        assert d['ts'] >= prev, f"chronology broken: {d['ts']} after {prev}"
        prev=d['ts']
        # Run logs under runtime/runs/ are ignored working state -- present on the machine
        # that produced the entry, absent in a fresh clone. Everything else must be there.
        if not d['evidence'].startswith('runtime/runs/'):
            assert os.path.exists(d['evidence']), f"evidence path missing: {d['evidence']}"
    except Exception as e:
        print(f"  ❌ line {i+1}: {e}"); bad+=1
print(f"  {'✅ PASS' if not bad else '❌ FAIL'}  lines valid: 9 fields, enums (cat/appr/outcome), ISO ts, chronological, evidence exists")
sys.exit(1 if bad else 0)
PY
  [ $? -eq 0 ] && pass=$((pass+1)) || fail=$((fail+1))
else
  echo "  ⚠️  python3 missing — schema validation SKIPPED (install python3; this is the module's core check)"
  fail=$((fail+1))
fi

echo ""; echo "── L3 No PII / secrets in the log ──"
check "no email addresses"        "! grep -qE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' $LOG"
check "no secret-looking keys"    "! grep -qiE 'password|api[_-]?key|secret|token' $LOG"

echo ""; echo "── L4 Rules documented (README) ──"
check "append-only rule"          "grep -qi 'append-only' logs/README.md"
check "no-PII rule"               "grep -qi 'no PII' logs/README.md"
check "correction mechanism"      "grep -q 'audit.correction' logs/README.md"
check "decision = new appended line" "grep -qi 'new lines' logs/README.md"
check "note is paraphrase, never verbatim" "grep -qi 'never verbatim' logs/README.md"
check "safe python append (no shell-echo)" "grep -q 'json.dumps(entry)' logs/README.md"
check "latest-state review recipe" "grep -q 'last={}' logs/README.md"
check "schema fields documented"  "grep -q '\`approval\`' logs/README.md && grep -q '\`evidence\`' logs/README.md"
check "honest scope note (no hooks)" "grep -qi 'convention' logs/README.md"

echo ""; echo "── L5 Skill present & disciplined ──"
check "SKILL.md exists"           "[ -f $SKILL ]"
check "append-only in skill"      "grep -qi 'Append-only' $SKILL"
# These three used to require the skill to teach an agent how to append a line by hand.
# That is the thing CLAUDE.md 3 forbids -- "a side effect of the gate, never something an
# agent chooses to write" -- and `runtime/audit.py` has no `append` command for the same
# reason. The checks were pinning the contradiction, so they now hold the rule instead.
check "skill refuses to write the log"  "grep -qi 'You never write to it' $SKILL"
check "skill cites the constitution"    "grep -q 'CLAUDE.md' $SKILL"
check "skill routes through the gate"   "grep -q 'company_runtime gate' $SKILL"
check "no hand-append recipe in skill"  "! grep -q 'json.dumps(entry)' $SKILL"
check "gate verb exists"                "python3 -m runtime.company_runtime --help 2>&1 | grep -q 'gate'"
check "SLA-start convention"      "grep -qi 'SLA clock starts' $SKILL"
check "red-flags section"         "grep -qi 'Red flags' $SKILL"
check "log-at-the-moment rule"    "grep -qi 'at the moment' $SKILL"

echo ""; echo "── L6 Wired into governance & org ──"
check "operating-principles §8 rule" "grep -qi 'audit log' company/operating-principles.md"
check "§8 carries removal note"      "grep -qi 'ships with the audit-log module' company/operating-principles.md"
check "COO owns periodic review"     "grep -q 'audit-log.jsonl' .claude/agents/coo-operations.md"
check "registered in routing-map"    "grep -q 'audit-log' company/routing-map.md"

echo ""; echo "── L7 The runtime writes the log itself (behaviour, not prose) ──"
if python3 -m unittest tests.test_audit >/dev/null 2>&1; then
  echo "  ✅ PASS  gated transitions produce their own audit line"; pass=$((pass+1))
else
  echo "  ❌ FAIL  gated transitions produce their own audit line"; fail=$((fail+1))
  python3 -m unittest tests.test_audit 2>&1 | tail -20
fi
check "writer exists in the runtime"  "[ -f runtime/audit.py ]"
check "chain verifies end to end"     "python3 -m runtime.audit verify >/dev/null"
check "gates call the writer"         "grep -q 'audit_log.append' runtime/company_runtime.py"

echo ""; echo "──── MODULE audit-log: $pass passed / $fail failed ────"
[ $fail -eq 0 ] || exit 1
