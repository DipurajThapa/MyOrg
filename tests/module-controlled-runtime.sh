#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
pass=0; fail=0
check(){ if eval "$2"; then echo "  ✅ PASS  $1"; pass=$((pass+1)); else echo "  ❌ FAIL  $1"; fail=$((fail+1)); fi; }
echo "── H1 Controlled runtime behavior ──"
# cygpath: Git Bash hands POSIX paths to native Windows Python, which reads /c/x as C:\c\x.
tmp=$(mktemp -d); tmp=$(cygpath -m "$tmp" 2>/dev/null || printf %s "$tmp")
trap 'rm -rf "$tmp"' EXIT; export MYORG_RUNS_DIR="$tmp"
cli="python3 runtime/company_runtime.py"; workflow="runtime/workflows/manual-gold-run.json"
check "workflow validates" "$cli validate $workflow >/dev/null"
created=$($cli create-run "$workflow" gold-run --actor chief-of-staff --request-id create-1)
revision=${created#*$'\t'}
check "initial step ready" "$cli status gold-run | grep -q $'frame-goal\\tready'"
check "dependency remains pending" "$cli status gold-run | grep -q $'produce-output\\tpending'"
check "wrong department cannot claim step" "! $cli request-step gold-run frame-goal --actor cto-engineering --request-id wrong-owner >/dev/null 2>&1"
$cli request-step gold-run frame-goal --actor chief-of-staff --request-id request-1 >/dev/null
check "green action enters progress" "$cli status gold-run | grep -q $'frame-goal\\tin_progress'"
check "stale result rejected" "! $cli complete gold-run frame-goal --actor chief-of-staff --request-id stale-1 --evidence README.md --revision wrong >/dev/null 2>&1"
$cli complete gold-run frame-goal --actor chief-of-staff --request-id complete-1 --evidence README.md --revision "$revision" >/dev/null
check "dependency becomes ready" "$cli status gold-run | grep -q $'produce-output\\tready'"
$cli request-step gold-run produce-output --actor cto-engineering --request-id request-2 >/dev/null
$cli fail gold-run produce-output --actor cto-engineering --request-id fail-1 --reason "verification failed" >/dev/null
check "bounded retry returns step once" "$cli status gold-run | grep -q $'produce-output\\tready.*attempts=1/2'"
$cli request-step gold-run produce-output --actor cto-engineering --request-id request-3 >/dev/null
$cli complete gold-run produce-output --actor cto-engineering --request-id complete-2 --evidence README.md --revision "$revision" >/dev/null
$cli request-step gold-run validate-output --actor coo-operations --request-id request-4 >/dev/null
$cli complete gold-run validate-output --actor coo-operations --request-id complete-3 --evidence README.md --revision "$revision" >/dev/null
before=$(wc -l < "$tmp/gold-run.jsonl")
$cli request-step gold-run release-output --actor chief-of-staff --request-id request-5 >/dev/null
check "yellow action waits for human" "$cli status gold-run | grep -q $'release-output\\tawaiting_approval'"
after_first=$(wc -l < "$tmp/gold-run.jsonl")
$cli request-step gold-run release-output --actor chief-of-staff --request-id request-5 >/dev/null
after_duplicate=$(wc -l < "$tmp/gold-run.jsonl")
check "request id is idempotent" "[ $before -lt $after_first ] && [ $after_first -eq $after_duplicate ]"
check "conflicting request id reuse is rejected" "! $cli approve gold-run release-output --approver human-owner --approval-ref session-approval --request-id request-5 >/dev/null 2>&1"
check "yellow cannot complete before approval" "! $cli complete gold-run release-output --actor chief-of-staff --request-id bypass-1 --evidence README.md --revision '$revision' >/dev/null 2>&1"
$cli approve gold-run release-output --approver human-owner --approval-ref session-approval --request-id approve-1 >/dev/null
$cli complete gold-run release-output --actor chief-of-staff --request-id complete-4 --evidence README.md --revision "$revision" >/dev/null
check "run completes after approval and evidence" "$cli status gold-run | grep -q 'status=completed'"
cp "$workflow" "$tmp/red.json"
sed -i 's/"publish"/"permanent_delete"/' "$tmp/red.json"
$cli create-run "$tmp/red.json" red-run --actor chief-of-staff --request-id red-create >/dev/null
red_revision=$($cli status red-run | head -1 | sed -E 's/.*revision=([^[:space:]]+).*/\1/')
$cli request-step red-run frame-goal --actor chief-of-staff --request-id red-1 >/dev/null
$cli complete red-run frame-goal --actor chief-of-staff --request-id red-2 --evidence README.md --revision "$red_revision" >/dev/null
$cli request-step red-run produce-output --actor cto-engineering --request-id red-3 >/dev/null
$cli complete red-run produce-output --actor cto-engineering --request-id red-4 --evidence README.md --revision "$red_revision" >/dev/null
$cli request-step red-run validate-output --actor coo-operations --request-id red-5 >/dev/null
$cli complete red-run validate-output --actor coo-operations --request-id red-6 --evidence README.md --revision "$red_revision" >/dev/null
$cli request-step red-run release-output --actor chief-of-staff --request-id red-7 >/dev/null
check "red action is blocked for human" "$cli status red-run | grep -q $'release-output\\tblocked_human'"
check "red action stops the run" "$cli status red-run | grep -q 'status=blocked_human'"
check "red action cannot be approved" "! $cli approve red-run release-output --approver human-owner --approval-ref no-bypass --request-id red-8 >/dev/null 2>&1"
$cli create-run "$workflow" retry-run --actor chief-of-staff --request-id retry-create >/dev/null
$cli request-step retry-run frame-goal --actor chief-of-staff --request-id retry-1 >/dev/null
$cli fail retry-run frame-goal --actor chief-of-staff --request-id retry-2 --reason "first failure" >/dev/null
$cli request-step retry-run frame-goal --actor chief-of-staff --request-id retry-3 >/dev/null
$cli fail retry-run frame-goal --actor chief-of-staff --request-id retry-4 --reason "second failure" >/dev/null
check "retry cap blocks repeated failure" "$cli status retry-run | grep -q 'status=blocked_retry_limit'"
cp "$workflow" "$tmp/cycle.json"
sed -i 's/"max_cycles": 12/"max_cycles": 1/' "$tmp/cycle.json"
$cli create-run "$tmp/cycle.json" cycle-run --actor chief-of-staff --request-id cycle-create >/dev/null
$cli request-step cycle-run frame-goal --actor chief-of-staff --request-id cycle-1 >/dev/null
check "cycle cap stops open-ended loop" "! $cli complete cycle-run frame-goal --actor chief-of-staff --request-id cycle-2 --evidence README.md --revision x >/dev/null 2>&1 && $cli status cycle-run | grep -q 'status=blocked_cycle_limit'"
cp "$tmp/gold-run.jsonl" "$tmp/tampered.jsonl"
sed -i '1s/chief-of-staff/cto-engineering/' "$tmp/tampered.jsonl"
check "event hash chain detects tampering" "! $cli status tampered >/dev/null 2>&1"
$cli create-run "$workflow" concurrent-run --actor chief-of-staff --request-id concurrent-create >/dev/null
($cli request-step concurrent-run frame-goal --actor chief-of-staff --request-id concurrent-a >/dev/null 2>&1) & first_pid=$!
($cli request-step concurrent-run frame-goal --actor chief-of-staff --request-id concurrent-b >/dev/null 2>&1) & second_pid=$!
wait "$first_pid"; first_rc=$?
wait "$second_pid"; second_rc=$?
concurrent_lines=$(wc -l < "$tmp/concurrent-run.jsonl")
check "concurrent claims serialize to one successor" "{ [ $first_rc -eq 0 ] && [ $second_rc -ne 0 ]; } || { [ $second_rc -eq 0 ] && [ $first_rc -ne 0 ]; }"
check "concurrent event stream remains valid" "$cli status concurrent-run | grep -q $'frame-goal\\tin_progress' && [ $concurrent_lines -eq 2 ]"
echo ""; echo "──── MODULE controlled-runtime: $pass passed / $fail failed ────"
[ $fail -eq 0 ] || exit 1
