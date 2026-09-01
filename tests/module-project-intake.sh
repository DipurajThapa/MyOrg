#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
pass=0; fail=0
check(){ if eval "$2"; then echo "  ✅ PASS  $1"; pass=$((pass+1)); else echo "  ❌ FAIL  $1"; fail=$((fail+1)); fi; }

echo "── I1 Governed project intake and production evidence ──"
process=docs/PROJECT-INTAKE-AND-PRODUCTION-LOOP.md
pack=templates/project-intake

check "canonical intake process exists" "test -s '$process'"
check "six staged documents are present" "test -s '$pack/00-intake-brief.template.md' && test -s '$pack/01-discovery-evidence.template.md' && test -s '$pack/02-value-stream-and-journey.template.md' && test -s '$pack/03-requirements-data-contract.template.md' && test -s '$pack/04-risk-and-controls.template.md' && test -s '$pack/05-test-release-readiness.template.md'"
check "MVP is capped at three capabilities" "grep -qiE 'maximum three|capped at three' '$process' '$pack/00-intake-brief.template.md'"
check "minimum intake has sponsor and decision owner" "grep -qi 'Sponsor:' '$pack/00-intake-brief.template.md' && grep -qi 'Decision owner:' '$pack/00-intake-brief.template.md'"
check "evidence labels include fact assumption and unknown" "grep -q 'FACT / ASSUMPTION' '$pack/01-discovery-evidence.template.md' && grep -q 'UNKNOWN' '$pack/01-discovery-evidence.template.md'"
check "missing baseline is not fabricated" "grep -qi 'no number was invented' '$pack/01-discovery-evidence.template.md' && grep -qi 'never backfill estimates' '$process'"
check "SIPOC and current/future maps are required" "grep -q '## SIPOC' '$pack/02-value-stream-and-journey.template.md' && grep -q '## Future-state map' '$pack/02-value-stream-and-journey.template.md'"
check "value stream measures touch wait rework and handoffs" "grep -qi 'Touch time.*Wait/queue time.*Rework.*Handoffs' '$pack/02-value-stream-and-journey.template.md'"
check "journey captures trust and evidence" "grep -qi 'Emotion/trust concern.*Evidence/event' '$pack/02-value-stream-and-journey.template.md'"
check "requirements trace outcomes to tests" "grep -q 'Outcome | Capability | Requirement.*Test.*Evidence' '$pack/03-requirements-data-contract.template.md'"
check "bidirectional contract owns authentication and idempotency" "grep -q 'AuthN/AuthZ.*Idempotency/correlation' '$pack/03-requirements-data-contract.template.md'"
check "maker checker and human owner remain distinct" "grep -qi 'Maker, checker, and human decision owner are distinct' '$pack/03-requirements-data-contract.template.md'"
check "control pack contains green yellow and red boundaries" "grep -qi 'Green actions' '$pack/04-risk-and-controls.template.md' && grep -qi 'Yellow actions' '$pack/04-risk-and-controls.template.md' && grep -qi 'Red actions' '$pack/04-risk-and-controls.template.md'"
check "control pack requires backup rollback and stop conditions" "grep -qi 'Backup, restore, and rollback' '$pack/04-risk-and-controls.template.md' && grep -qi 'Stop/disable conditions' '$pack/04-risk-and-controls.template.md'"
check "release results distinguish blocked and not run" "grep -q 'Passed/Failed/Blocked/Not run' '$pack/05-test-release-readiness.template.md'"
check "release gate requires security privacy and accessibility" "grep -qi 'Security/privacy/accessibility reviews complete' '$pack/05-test-release-readiness.template.md'"
check "release gate requires observability and recovery" "grep -qi 'Monitoring, runbook, support, backup/restore, and rollback verified' '$pack/05-test-release-readiness.template.md'"
check "production decision is honestly blocked" "grep -q 'Release decision: BLOCKED' '$process' && grep -qi 'must not be described as production-ready' '$process'"

echo ""; echo "──── MODULE project-intake: $pass passed / $fail failed ────"
[ $fail -eq 0 ] || exit 1
