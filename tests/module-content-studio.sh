#!/usr/bin/env bash
# MODULE suite — Content Studio (youtube-script-writer). Optional/dormant example module.
# Validates the skill, its worked run under examples/, and module registration. Run by tests/run.sh.
# If you remove the Content Studio module, delete this file too — core.sh stays green without it.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
RUN="examples/content-studio/runs/ai-automation-small-business"
SC="$RUN/02-scripts"
SKILL="examples/content-studio/youtube-script-writer"   # dormant — activate by copying into .claude/skills/
AGENT="examples/content-studio/head-of-content.md"      # dormant — activate by copying into .claude/agents/
pass=0; fail=0
check(){ if eval "$2"; then echo "  ✅ PASS  $1"; pass=$((pass+1)); else echo "  ❌ FAIL  $1"; fail=$((fail+1)); fi; }

echo "── M1 Capability files ──"
check "SKILL.md"              "[ -f $SKILL/SKILL.md ]"
check "≥6 reference files"    "[ \$(ls $SKILL/references/*.md | wc -l) -ge 6 ]"
check "channel-profile"       "[ -f $SKILL/config/channel-profile.md ]"
check "head-of-content agent" "[ -f $AGENT ]"

echo ""; echo "── M2 Research leg ──"
check "research-brief"        "[ -f $RUN/00-research/research-brief.md ]"
check "≥4 sources cited"      "[ \$(grep -c 'https://' $RUN/00-research/research-brief.md) -ge 4 ]"
check "GEO question surface"  "grep -qi 'GEO question surface' $RUN/00-research/research-brief.md"

echo ""; echo "── M3 Series (15 eps) ──"
check "exactly 15 episodes"        "[ \$(grep -c '^### Ep ' $RUN/01-series/series-blueprint.md) -eq 15 ]"
check "15 GEO questions owned"      "[ \$(grep -c 'GEO question owned' $RUN/01-series/series-blueprint.md) -eq 15 ]"
check "binge loop (Watch next)"     "[ \$(grep -c 'Watch next' $RUN/01-series/series-blueprint.md) -ge 14 ]"

echo ""; echo "── M4 All 15 scripts present & structured ──"
check "15 script files exist"  "[ \$(find $SC -name 'ep*.md' | wc -l) -eq 15 ]"
miss=0
for n in 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15; do
  f=$(find $SC -name "ep${n}-*.md" 2>/dev/null | head -1)
  if [ -z "$f" ]; then echo "     · missing ep$n"; miss=$((miss+1)); fi
done
check "no episode number missing"  "[ $miss -eq 0 ]"
nohook=0; nogeo=0; nometa=0
for f in $(find $SC -name 'ep*.md'); do
  grep -qi 'COLD-OPEN HOOK' "$f" || nohook=$((nohook+1))
  grep -qi 'GEO ANSWER BLOCK' "$f" || nogeo=$((nogeo+1))
  grep -qi 'Chapters' "$f" || nometa=$((nometa+1))
done
check "every script has a cold-open hook"  "[ $nohook -eq 0 ]"
check "every script has a GEO answer block" "[ $nogeo -eq 0 ]"
check "every script has chapters metadata"  "[ $nometa -eq 0 ]"

echo ""; echo "── M5 Growth plan & thumbnails ──"
check "growth-plan"          "[ -f $RUN/04-growth/growth-plan.md ]"
check "GEO distribution"     "grep -qi 'GEO distribution' $RUN/04-growth/growth-plan.md"
check "thumbnail ideas (15)" "[ \$(grep -c '^### Ep ' $RUN/03-thumbnails/thumbnail-ideas.md) -eq 15 ]"

echo ""; echo "── M6 Governance (module) ──"
check "ep01 approval gate"   "grep -qi 'approval' $SC/act-1-hook/ep01-save-20-hours.md"
check "growth approval gate" "grep -qi 'requires your explicit approval' $RUN/04-growth/growth-plan.md"
check "skill no-publish rule" "grep -qi 'without explicit human approval' $SKILL/SKILL.md"

echo ""; echo "── M7 Module registration ──"
check "registered in routing-map" "grep -q 'Content Studio — Head of Content' company/routing-map.md"
check "agent routes the skill"    "grep -q 'youtube-script-writer' $AGENT"
check "module documented as dormant" "grep -qi 'dormant' company/routing-map.md"

echo ""; echo "── M8 Mode switch ──"
det(){ grep -q '<UNSET>' "$1" && echo GENERAL || echo DEDICATED; }
check "template → general"         "[ \$(det $SKILL/config/channel-profile.TEMPLATE.md) = GENERAL ]"
check "active profile → dedicated" "[ \$(det $SKILL/config/channel-profile.md) = DEDICATED ]"
check "example → dedicated"        "[ \$(det $SKILL/config/channel-profile.EXAMPLE.md) = DEDICATED ]"

echo ""; echo "── M9 Navigability (INDEX manifest) ──"
check "INDEX manifest exists" "[ -f $RUN/INDEX.md ]"

echo ""; echo "──── MODULE content-studio: $pass passed / $fail failed ────"
[ $fail -eq 0 ] || exit 1
