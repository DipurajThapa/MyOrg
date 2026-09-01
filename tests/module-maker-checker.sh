#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
pass=0; fail=0
check(){ if eval "$2"; then echo "  ✅ PASS  $1"; pass=$((pass+1)); else echo "  ❌ FAIL  $1"; fail=$((fail+1)); fi; }
echo "── X1 Typed exchange and maker-checker ──"
# cygpath: Git Bash hands POSIX paths to native Windows Python, which reads /c/x as C:\c\x.
tmp=$(mktemp -d); tmp=$(cygpath -m "$tmp" 2>/dev/null || printf %s "$tmp")
evidence=runtime/runs/test-maker.evidence; checker_evidence=runtime/runs/test-checker.evidence; trap 'rm -rf "$tmp"; rm -f "$evidence" "$checker_evidence"' EXIT
export MYORG_RUNS_DIR="$tmp"; cp runtime/fixtures/maker-output.md "$evidence"; cp runtime/fixtures/maker-output.md "$checker_evidence"
cli="python3 runtime/company_runtime.py"; workflow=runtime/workflows/maker-checker-gold-run.json
check "maker-checker workflow validates" "$cli validate $workflow >/dev/null"
cp "$workflow" "$tmp/same-role.json"; sed -i 's/"checker":"coo-operations"/"checker":"cto-engineering"/' "$tmp/same-role.json"
check "maker cannot also be checker" "! $cli validate '$tmp/same-role.json' >/dev/null 2>&1"
cp "$workflow" "$tmp/checker-on-yellow.json"; sed -i 's/"action":"internal_write"/"action":"publish"/' "$tmp/checker-on-yellow.json"
check "checker must precede separate human-gated action" "! $cli validate '$tmp/checker-on-yellow.json' >/dev/null 2>&1"
created=$($cli create-run "$workflow" exchange-run --actor chief-of-staff --request-id x-create); revision=${created#*$'\t'}
$cli request-step exchange-run frame-goal --actor chief-of-staff --request-id x-frame-start >/dev/null
$cli complete exchange-run frame-goal --actor chief-of-staff --request-id x-frame-done --evidence docs/EXCHANGE-MAKER-CHECKER-AUDIT.md --revision "$revision" >/dev/null
check "maker step becomes ready" "$cli status exchange-run | grep -q $'produce-output\\tready'"
$cli send-message exchange-run produce-output question-1 --from-agent cto-engineering --to-agent coo-operations --kind question --subject "Clarify acceptance evidence" --payload "$evidence" --classification internal --request-id x-question >/dev/null
$cli send-message exchange-run produce-output answer-1 --from-agent coo-operations --to-agent cto-engineering --kind answer --subject "Acceptance evidence clarified" --payload docs/EXCHANGE-MAKER-CHECKER-AUDIT.md --classification internal --reply-to question-1 --request-id x-answer >/dev/null
check "two-way thread is recorded" "$cli status exchange-run --json | grep -q 'question-1' && $cli status exchange-run --json | grep -q 'answer-1'"
check "reply must reverse direction" "! $cli send-message exchange-run produce-output bad-reply --from-agent cto-engineering --to-agent coo-operations --kind answer --subject bad --payload '$evidence' --classification internal --reply-to question-1 --request-id x-bad-reply >/dev/null 2>&1"
check "unrelated agent cannot join step exchange" "! $cli send-message exchange-run produce-output bad-party --from-agent cmo-marketing --to-agent coo-operations --kind handoff --subject bad --payload '$evidence' --classification internal --request-id x-bad-party >/dev/null 2>&1"
check "restricted classification cannot enter event state" "! $cli send-message exchange-run produce-output restricted-1 --from-agent cto-engineering --to-agent coo-operations --kind handoff --subject bad --payload '$evidence' --classification restricted --request-id x-restricted >/dev/null 2>&1"
$cli request-step exchange-run produce-output --actor cto-engineering --request-id x-make-start >/dev/null
$cli complete exchange-run produce-output --actor cto-engineering --request-id x-submit --evidence "$evidence" --revision "$revision" >/dev/null
check "maker submission waits for checker" "$cli status exchange-run | grep -q $'produce-output\\tawaiting_check'"
check "downstream remains blocked before check" "$cli status exchange-run | grep -q $'release-output\\tpending'"
$cli send-message exchange-run produce-output decision-1 --from-agent coo-operations --to-agent cto-engineering --kind decision --subject "Submission approved" --payload docs/EXCHANGE-MAKER-CHECKER-AUDIT.md --classification internal --request-id x-decision >/dev/null
check "maker cannot approve own submission" "! $cli check-approve exchange-run produce-output --actor cto-engineering --message-id decision-1 --request-id x-self-check >/dev/null 2>&1"
$cli check-approve exchange-run produce-output --actor coo-operations --message-id decision-1 --request-id x-check >/dev/null
check "checker approval releases downstream" "$cli status exchange-run | grep -q $'release-output\\tready'"
$cli send-message exchange-run release-output handoff-1 --from-agent cto-engineering --to-agent chief-of-staff --kind handoff --subject "Checked output ready for release decision" --payload "$evidence" --classification internal --request-id x-handoff >/dev/null
$cli send-message exchange-run release-output handoff-reply-1 --from-agent chief-of-staff --to-agent cto-engineering --kind question --subject "Confirm release evidence" --payload docs/EXCHANGE-MAKER-CHECKER-AUDIT.md --classification internal --reply-to handoff-1 --request-id x-handoff-reply >/dev/null
check "adjacent workflow legs exchange in both directions" "$cli status exchange-run --json | grep -q 'handoff-1' && $cli status exchange-run --json | grep -q 'handoff-reply-1'"
check "exchange stores references not payload text" "! grep -q 'Fictional internal artifact' '$tmp/exchange-run.jsonl'"

$cli create-run "$workflow" rework-run --actor chief-of-staff --request-id r-create >/dev/null
rework_revision=$($cli status rework-run | head -1 | sed -E 's/.*revision=([^[:space:]]+).*/\1/')
$cli request-step rework-run frame-goal --actor chief-of-staff --request-id r-frame-start >/dev/null
$cli complete rework-run frame-goal --actor chief-of-staff --request-id r-frame-done --evidence docs/EXCHANGE-MAKER-CHECKER-AUDIT.md --revision "$rework_revision" >/dev/null
for round in 1 2 3; do
  $cli request-step rework-run produce-output --actor cto-engineering --request-id "r-start-$round" >/dev/null
  $cli complete rework-run produce-output --actor cto-engineering --request-id "r-submit-$round" --evidence "$evidence" --revision "$rework_revision" >/dev/null
  $cli send-message rework-run produce-output "feedback-$round" --from-agent coo-operations --to-agent cto-engineering --kind feedback --subject "Correction round $round" --payload docs/EXCHANGE-MAKER-CHECKER-AUDIT.md --classification internal --request-id "r-feedback-$round" >/dev/null
  $cli check-return rework-run produce-output --actor coo-operations --message-id "feedback-$round" --request-id "r-return-$round" >/dev/null
done
check "review loop stops after configured returns" "$cli status rework-run | grep -q 'status=blocked_review_limit'"
submission_count=$($cli status rework-run --json | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["steps"]["produce-output"]["submissions"]))')
check "all maker revisions remain attributable" "[ $submission_count -eq 3 ]"

$cli create-run "$workflow" stale-check-run --actor chief-of-staff --request-id s-create >/dev/null
stale_revision=$($cli status stale-check-run | head -1 | sed -E 's/.*revision=([^[:space:]]+).*/\1/')
$cli request-step stale-check-run frame-goal --actor chief-of-staff --request-id s-frame-start >/dev/null
$cli complete stale-check-run frame-goal --actor chief-of-staff --request-id s-frame-done --evidence docs/EXCHANGE-MAKER-CHECKER-AUDIT.md --revision "$stale_revision" >/dev/null
$cli request-step stale-check-run produce-output --actor cto-engineering --request-id s-start >/dev/null
$cli complete stale-check-run produce-output --actor cto-engineering --request-id s-submit --evidence "$evidence" --revision "$stale_revision" >/dev/null
$cli send-message stale-check-run produce-output stale-decision --from-agent coo-operations --to-agent cto-engineering --kind decision --subject "Approve only unchanged artifact" --payload "$checker_evidence" --classification internal --request-id s-decision >/dev/null
printf '\nchanged checker decision evidence\n' >> "$checker_evidence"
check "checker decision rejects changed rationale artifact" "! $cli check-approve stale-check-run produce-output --actor coo-operations --message-id stale-decision --request-id s-check-message >/dev/null 2>&1"
cp runtime/fixtures/maker-output.md "$checker_evidence"
printf '\nchanged after handoff\n' >> "$evidence"
check "checker rejects maker artifact changed after handoff" "! $cli check-approve stale-check-run produce-output --actor coo-operations --message-id stale-decision --request-id s-check-maker >/dev/null 2>&1"
echo ""; echo "──── MODULE maker-checker: $pass passed / $fail failed ────"
[ $fail -eq 0 ] || exit 1
