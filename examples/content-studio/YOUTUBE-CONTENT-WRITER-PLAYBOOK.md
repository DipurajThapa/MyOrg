# 📹 YouTube Content Writer — Process Document

**What it is:** a Content Studio capability that turns any topic into researched, bingeable,
SEO **and** GEO-optimized YouTube content — a 15-episode series, full scripts, and a growth
plan to increase subscribers, viewers, and followers. It runs as a **general** writer out of the
box, and converts into **your channel's dedicated** writer by filling one file.

- **Skill:** `.claude/skills/youtube-script-writer/`
- **Runs via:** the `head-of-content` agent, or just ask in plain language.
- **Proven:** see `tests/ACCEPTANCE.md` (42/42 checks) and a real run in
  `runs/ai-automation-small-business/`.

---

## Part 1 — How to use it (end-to-end)

### Step 1 · Ask
Open a `claude` session in this folder and say what you want, e.g.:
> *"Write me a YouTube series on home espresso for beginners."*
> *"Research 'personal finance for Gen Z' and build a 15-episode series with scripts."*
> *"Grow my channel — I make videos about AI tools."*

The Chief of Staff routes it to **Head of Content**, which runs the pipeline.

### Step 2 · What it does (the pipeline)
1. **Intake** — confirms your topic; loads your channel profile if you have one.
2. **Research** — live web search for keywords, competitors, and the real *questions people ask
   AI engines* (your GEO targets). Cites sources; never invents stats.
3. **Series architecture** — a 15-episode arc (Hook → Foundations → Application → Advanced →
   Capstone), each episode owning one question, chained into a binge loop.
4. **Scripts** — writes the flagship Episode 1 in full (and any episode you name); retention-
   engineered (hook, open loops, pattern interrupts, subscribe moments) and GEO-structured
   (direct spoken answer in the first 30s = citable transcript).
5. **Growth plan** — packaging, retention, binge strategy, Shorts funnel, GEO distribution,
   and the 5 metrics to watch with target benchmarks.

### Step 3 · What you get (files)
Everything is written to `runs/<your-topic>/`:
```
research-brief.md    ← keywords, competitors, GEO questions, sourced stats
series-blueprint.md  ← all 15 episodes with titles, hooks, metadata, thumbnails
ep01-script.md       ← full flagship script (ask for more episodes any time)
growth-plan.md       ← how to grow subs/viewers/followers + 30-day plan
```
Plus a tight summary in chat.

### Step 4 · Get more scripts
> *"Now write the full script for Episode 8."*
It generates that episode on demand (you film one at a time — this keeps each script deep).

### Step 5 · Publish (human-gated — important)
The studio **does not publish or upload anything.** You get finished scripts and a plan; **you**
record and hit publish. Before publishing, run the checklist in Part 4.

---

## Part 2 — Convert it into YOUR channel's dedicated writer

This is the "general tool → the channel's content writer" switch. **One file, no code.**

### The mechanism
The skill checks `.claude/skills/youtube-script-writer/config/channel-profile.md` on every run:
- **Contains any `<UNSET>`** → **general mode** (broad best practices; states assumptions).
- **Fully filled (zero `<UNSET>`)** → **dedicated mode** (your voice, audience, format, funnel).

*(Deterministic: `grep -q '<UNSET>' channel-profile.md` → if it matches, general.)*

### The 3-step conversion

**Step A · Let Claude interview you (easiest).**
> *"Set up the YouTube writer for my channel."*
Head of Content walks you through each profile field and writes your answers into
`channel-profile.md`. Or edit the file yourself — a filled reference is in
`config/channel-profile.EXAMPLE.md` ("The Solo Automator").

The fields, and what each one changes:
| You provide | It changes |
|---|---|
| Niche + positioning | Topic angle, which sub-queries to own, differentiation |
| Audience + "how they talk" | Keyword choice, GEO question phrasing, examples, objections |
| Tone + banned words + signature phrases | The literal voice; intro/outro |
| Primary goal + CTA + funnel | Subscribe vs. lead-gen framing; where the CTA lands |
| Format + length + cadence | Pacing, segment count, Shorts cut points, publish plan |
| **Proprietary frameworks / data** | Your GEO "original element" — citation bait competitors can't copy |
| Compliance notes | Auto-flags claims needing legal/financial/medical review |

**Step B · Add your proof (do not skip).**
Fill "proprietary frameworks / data / case studies you own." This is the single biggest GEO
lever — original material is what AI engines cite over lookalike sources, and what makes your
videos un-clonable.

**Step C · Calibrate the voice (2 iterations).**
> *"Generate Episode 1 for my channel."*
Read it aloud. Where it doesn't sound like you, tell Claude — it updates the profile's Voice
section. Two rounds usually locks it in. From then on, every script sounds like your channel.

### Multiple channels
Keep one profile file per channel (e.g. `channel-profile.cooking.md`) and tell the skill which
to load. Also store the active profile in company memory so the CMO / brand-voice department
stays consistent with your channel voice.

---

## Part 3 — The growth loop (turn scripts into subscribers)

1. **Package hard.** 3 title+thumbnail options for the flagship; test thumbnails. CTR target 4–6%+.
2. **Publish as a playlist series**, each episode end-screening into the next (session time lifts
   the whole channel).
3. **Cut 2–3 Shorts** per episode → route to the long video (Shorts grow followers fast).
4. **Repurpose every transcript into a companion article/FAQ** (first 200 words = the answer) →
   gets you cited by AI engines → new visitors → subscribers YouTube alone won't reach (**GEO**).
5. **Reply to every comment in hour 1**; pin the video's question.
6. **Hold a cadence** (1–2/week). Batch-produce so you never slip.
Full detail: `.claude/skills/youtube-script-writer/references/optimization-playbook.md`.

---

## Part 4 — Pre-publish checklist (you run this)
- [ ] **Re-verify every stat** against a current source (the script flags them).
- [ ] Thumbnail readable at phone size; ≤3 words; title ≤60 chars, keyword front-loaded.
- [ ] Chapters added; description first 2 lines = the direct answer.
- [ ] Added to the series playlist; end screen points to the next episode.
- [ ] Any regulated claim (financial/medical/legal) reviewed — loop in the `clo-legal` agent.
- [ ] Companion article drafted for GEO.

---

## Part 5 — FAQ / troubleshooting
- **"It wrote generic content."** Your channel profile is still `<UNSET>` → it's in general mode.
  Fill `channel-profile.md` (Part 2).
- **"I want all 15 scripts now."** Ask for them; by default it writes Ep 1 in full so each script
  stays deep. Batch them: *"write full scripts for Episodes 2 through 5."*
- **"Stats might be stale."** The research leg is dated to run day; always re-verify at publish.
- **"Can it post for me?"** No — publishing is human-gated by design. It produces; you publish.
- **"How do I measure GEO?"** YouTube analytics can't see AI citations; use an external
  LLM-visibility tracker or a "how did you find us?" survey. Treat it as a leading indicator.

---
*Re-run the acceptance suite any time: `bash tests/run.sh`.*
