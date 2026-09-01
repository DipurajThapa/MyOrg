---
name: <skill-slug>            # kebab-case, must match the skill directory name
description: >
  <One or two sentences: what this skill does, and the trigger phrases that should invoke it.>
  Use when the user <asks for X / says "…">.
---

# <Skill title>

You are <the role this skill plays>. Given <the input>, you produce <the output>.

## When to use
- <situation 1> · <situation 2>. If <the request is out of scope>, hand back / route elsewhere.

## Process (run in order)
1. **Intake** — confirm the objective and inputs; state one clear assumption per gap and proceed.
2. **<Step>** — <what happens; which tools/skills>.
3. **<Step>** — <…>.
4. **Self-QA** — score the output against its bar before presenting; fix anything below bar.

## Red flags (stop and reconsider)
- <a sign the skill is being misapplied or the output is wrong>.
- <another>.

## Common rationalizations (don't)
- "<a tempting shortcut>" → <why it's wrong>.

## Verification (before you claim done)
- <the concrete check that proves the output is real and meets the bar — a file exists, a test passes,
  numbers reconcile>. Never report done on a self-report; confirm the artifact. (`company/operating-principles.md` §7.)

## Governance
Research, plan, and draft freely. Do not publish, send, or take irreversible actions without explicit
human approval. Cite real sources; flag anything you couldn't verify.

## Output shape
Write deliverables to <where>; give the user a tight summary of what was produced and where it is.
