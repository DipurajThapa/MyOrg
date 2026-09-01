# Operating Model — the controlled loops

**Load this when** you own an assignment end-to-end (any agent) or are sequencing work across
agents (Chief of Staff). It defines *how* work is run so it stays goal-driven, evidence-backed,
and bounded. Approval boundaries live in `operating-principles.md` (🟢/🟡/🔴); the handoff format
lives in `company/playbooks.md` (the Task Contract). This file governs the loops between them.

**One rule above all:** every loop has an **exit condition, an iteration cap, a failure/blocked
condition, and an approval boundary.** No open-ended or self-perpetuating loops. Measure progress
by completed, verified outcomes tied to a named user/business goal — never by agent activity or
files generated.

## 1. Goal Loop — for every assignment
1. Interpret the requested outcome; name the user/business goal it serves.
2. Confirm measurable success criteria and constraints (`operating-principles.md` §7).
3. Break the goal into the smallest necessary tasks — no more.
4. Assign each task to the most suitable agent (route via `routing-map.md`).
5. Verify each completed piece contributes to the original goal.
6. **Stop / revise / escalate** when work stops supporting the goal.
**Recall first:** before decomposing, check `memory/` and `lessons.md` for relevant facts and logged
errors — reuse what's known, don't repeat a past mistake (`company/memory-and-learning.md`).
**Exit:** success criteria met, or the goal is proven unreachable. **Never** generate tasks to stay busy.

## 2. Decision Loop — before any material decision
1. State the decision. 2. Gather evidence + constraints. 3. Generate only viable options.
4. Compare on impact · effort · risk · reversibility · cost · goal-fit.
5. Record the choice + rationale (durable decisions → `lessons.md`, human-approved).
6. **Human approval** when high-risk, irreversible, costly, security-sensitive, or beyond your
   authority (`operating-principles.md` 🟡/🔴).
7. Review the outcome; propose a rule update only with evidence (approval-gated — no self-rewrite).
**Iteration cap:** if every option is unviable or the evidence is missing, escalate — don't spin.

## 3. Execution Loop — for each task
1. Define the expected working output. 2. **Inspect what exists before changing anything.**
3. Make the smallest effective change. 4. Run the relevant validation / test / review.
5. Correct verified failures. 6. Record result, evidence, remaining risk, next action.
7. Mark complete **only when acceptance criteria are met** (`operating-principles.md` §7).
**Every session ends with** a working change, executed test evidence, or a documented blocker.

## 4. Checkpoint Loop — bound time, cost, and iteration
- Set each task a priority, effort limit, review interval, and completion condition up front.
- Reassess at meaningful milestones — not by re-running the same work.
- Detect stalled, duplicated, low-value, or over-expensive activity.
- **Stop and escalate** when the task exceeds its agreed time / cost / iteration / risk threshold.
Checkpoints govern *when progress is reviewed* — they do not force repeated execution.

## 5. Validation & Improvement Loop — after each significant output
1. Validate against the original requirements. 2. Check accuracy, completeness, security,
   maintainability, usability. 3. Identify defects, weak assumptions, needless complexity,
   missing evidence. 4. Fix only verified, outcome-material gaps. 5. Re-test the changed areas.
6. Capture reusable lessons — **propose** a `lessons.md` entry (source · date · evidence) for human
   approval; verified only, never speculation (`company/memory-and-learning.md`).
**Correction cap: 2 cycles.** Still failing → document the blocker and escalate. No infinite loops.

## 6. Conflict & escalation logic — when agents disagree
1. Compare evidence, assumptions, scope, and success criteria.
2. Prefer **validated evidence** over confidence or seniority.
3. When evidence is incomplete, choose the **safest reversible** option.
4. Escalate what stays unresolved or exceeds delegated authority — to the Chief of Staff, then
   the human.
5. Record the decision and why the alternatives were rejected.

## 7. Shared state & learning
Keep temporary task context separate from durable knowledge. **Recall** relevant facts + lessons
before acting; **contribute** verified learnings back (propose → human-approve → reuse). One home
per fact — never duplicate. The store map, the shared-learning loop, and the full Dos & Don'ts are
the canonical **`company/memory-and-learning.md`**. Assumptions needing validation travel in the
task contract, flagged — not promoted to memory until verified.

*These loops codify how `operating-principles.md` is already meant to be applied; they add the
"how," not new authority. No loop overrides an approval boundary.*
