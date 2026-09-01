# Acceptance Test Report — YouTube Content Writer + Org Integration

**Date:** 2026-07-11 · **System:** `/Users/dipurajthapa/Agents/Enterprise`
**Test input:** "AI automation for small business owners" (general mode)
**Method:** executed checks via Bash + Python (not assertions). Re-run: `bash tests/run.sh`.

## Verdict
**PASS — 28/28 suite checks green**, covering the capability, org integration, the mode switch,
and a **complete 15-episode production** (all scripts present, substantial, and structurally
valid). Reached after **2 real find-and-fix iterations** (see log). Nothing was published or
sent; all outward actions remain human-gated. Limitations documented below.

## What was built and verified
1. **Capability** — `youtube-script-writer` skill (SKILL.md + 7 references + channel-profile
   config) and the `head-of-content` agent, registered into the org (`CLAUDE.md`).
2. **A full run** on the test topic, organized for organic fetching:
   `examples/content-studio/runs/ai-automation-small-business/` → `00-research/`, `01-series/`, `02-scripts/<act>/`,
   `03-thumbnails/`, `04-growth/`, `INDEX.md`.
3. **All 15 scripts** written (flagship by the main loop; the other 14 by a 7-agent parallel
   swarm), each 1,296–2,274 words (avg 1,946; ~29,200 total), each carrying hook + GEO answer
   block + subscribe moment + next-episode handoff + full metadata + thumbnail concept.
4. **Thumbnail briefs** for all 15 (a consistent series "shelf" system).

## Acceptance criteria → result
| # | Criterion | Verified by | Result |
|---|---|---|---|
| AC1 | Capability installed & loadable | T1 + frontmatter parse (15 agents + skill) | ✅ |
| AC2 | Live research, real sources | T2 + `00-research/research-brief.md` (6 URLs) | ✅ |
| AC3 | 15-episode series, one GEO question each, binge loop | T3 | ✅ |
| AC4 | **All 15 full scripts present & structured** | T4 (count=15, no gaps, hook+GEO+chapters each) | ✅ |
| AC5 | Growth plan + thumbnails (15) | T5 | ✅ |
| AC6 | Governance: nothing published without approval | T6 | ✅ |
| AC7 | Org integration (dept + routing + 15 agents) | T7 | ✅ |
| AC8 | General→Dedicated mode switch deterministic | T8 | ✅ |
| AC9 | Navigable manifest for organic fetching | T9 (INDEX.md) | ✅ |
| AC10 | Frontmatter valid & parseable | Python YAML, 16/16 | ✅ |

## Iteration log (the tests did their job)
- **Iter 1 — mode-switch bug.** Default channel profile misdetected as "dedicated" (brittle
  regex). Hardened the `<UNSET>` sentinel rule in the skill + fixed the detector. Re-ran → pass.
- **Iter 2 — a swarm agent silently no-op'd.** During parallel script generation, the agent
  assigned Ep 2 & Ep 3 completed **without writing its files** (it echoed the exemplar instead of
  producing new content). Caught by an on-disk file check, **not** by trusting the agent's
  "completed" status. Re-dispatched as two focused single-script agents with a tightened prompt
  ("write the file, do not echo Ep 1") → both produced. Final count verified 15/15 on disk.
  *Lesson encoded: verify artifacts on disk, never trust a subagent's self-report.*

## Production evidence (executed)
```
15/15 scripts on disk · avg 1,946 words · 0 thin (<900w) · every beat present (hook/GEO/subs/next/tags/thumbnail)
Suite: 28 passed / 0 failed (exit 0)
Frontmatter: 16/16 valid
```

## Unresolved limitations & honest caveats
1. **Illustrative worked-examples ≠ research stats.** A few scripts use example dollar figures
   (e.g. payback math) — the writing flags these as illustrative. Only the three sourced stats
   (77% adoption / +40% productivity / 66% save $500–$2,000/mo) are presented as data.
2. **Stats dated to run day (2026-07-11);** re-verify before publishing.
3. **Scripts vary in length/voice slightly** (different agents wrote different acts). They match
   structure and house style; a light human editorial pass is recommended for a single voice.
4. **Tool prices/capabilities move fast** — Zapier/MindStudio/Gemini specifics need a pre-publish check.
5. **No YouTube connectors exercised** — publishing and live analytics remain human-gated by design.
6. **GEO citation impact isn't measurable in YouTube analytics** — needs an external tracker.

## Human-approval-required (never auto-done)
Publishing/uploading to YouTube · sending outreach · spend on tools/ads. The studio produces
scripts and plans; a human executes.

## How to re-run
`bash tests/run.sh` (structural/functional). The research leg is re-exercised by invoking the
skill on a topic (live web search).
