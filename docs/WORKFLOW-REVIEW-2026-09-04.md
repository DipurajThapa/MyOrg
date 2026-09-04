# End-to-end workflow review — 2026-09-04

One stage at a time, from a person typing a sentence to the company finishing the work.
Each stage records what happens, what was wrong, what was fixed, and what was deferred
because it belongs to a later stage.

---

## Stage 1 — Idea entry

**Path.** `console.html startWork()` → `POST /v1/ideas` → `service_runs.submit_idea` →
`triggers.enqueue` → `db_triggers.enqueue_trigger` → one `trigger_intake` row, `queued`.

**Inputs.** One goal string (10..500 printable characters), a bearer token, an
`X-Request-Id`.
**Outputs.** `{intake_id, run_id, status, goal, created}`. The run id is derived, not
created — no run exists yet.
**Decision points.** Role check · human check · shape and length check · queue-depth
refusal at 50 · idempotent insert on `intake_id`.
**Handoff.** A `queued` row. The scheduler's intake pass is the only reader.

### Fixed

| Issue | Fix |
|---|---|
| `submit_idea` was the only work-creating operator route with no `actor_type == "human"` check. An agent holding `chief-of-staff` could write its own goal text, queue it, and have the company plan and run it. This is the single route where free goal text reaches the planner — `triggers.py` states plainly that a payload naming its own goal is an instruction from outside the trust boundary. | One check, matching every sibling route. Test: `test_only_a_human_may_put_free_text_in_front_of_the_planner`. |
| The console minted a fresh `X-Request-Id` per click. Since the intake id is a hash of that id, a dropped connection after the server had queued the work left the operator retrying into a second row, a second plan and a second bill — the dedup machinery existed and the client defeated it. | The console holds one request id per wording. Same sentence retried = same request; changed sentence = new one. |
| The console reported `Queued as X` even when the row already existed. | It now says which of the two happened. |
| `api_routes.py` claimed the console route is refused unless the actor is a human. It is not — only `MYORG_CONSOLE_ACTOR` being set and a loopback caller are enforced. | Comment corrected to say what the code does, and to point at the routes that do check. |

### Known and accepted

- **Queue-depth check is not atomic with the insert.** `enqueue` counts in one transaction
  and inserts in another, so two simultaneous submits at depth 50 can both pass. Harmless
  at one operator; revisit only if the API gains concurrent writers.

### Verified after the fixes

16 checks over the real HTTP boundary (`stage1_live.py`), not just unit tests: the console
token is the named human · a new idea answers `202` and a replay answers `200` on the same
row · a different wording is a different row · an agent with `chief-of-staff` is refused
`403` · the list is oldest-first and carries no leaked row · the store agrees with the API.
Full suite green.

One thing the check corrected rather than confirmed: the route already answers **202 Accepted**
for a new idea and **200 OK** for a replay. The status code was carrying the `created` flag
all along; only the page was ignoring it.

### Carried forward

| ID | Issue | Revisit at |
|---|---|---|
| C-1 | **A queued idea cannot be withdrawn.** Between submit and the next sweep, a mistyped or regretted goal is unstoppable and will be planned at real cost. `cancel_run` needs a run, which does not exist yet. `settle_trigger` only matches `status='queued'`, so a withdraw would be naturally race-safe against the sweeper — but `trigger_intake.status` has no `withdrawn` value, and reusing `failed` would make `escalate_ideas` notify the operator that their own withdrawal "could not be planned". | Stage 3 (cancellation), so intake-withdraw and run-cancel are designed as one idea rather than two. |
| C-2 | **A permanently transient failure at the front of the queue freezes everything behind it.** A transient failure deliberately spends no attempt, so it retries forever; under the serial queue below, nothing else starts while it holds the front. `escalate_stuck_ideas` tells the operator, but with no withdraw (C-1) there is nothing they can do about it. C-1 is also the fix for this. | Stage 3, with C-1. |

---

## Stage 1a — Queue discipline

Asked for after Stage 1: **first in, first out, one at a time.**

**What already matched.** Three attempts then give up (`MAX_TRIGGER_ATTEMPTS`), the reason
recorded rather than overwritten with a count, and the next request promoted. A retry keeps
its place, because `settle_trigger` never touches `created_at`.

**What was missing.** The queue was a pile, not a line: one pass started up to five requests
and the drive pass moved them all at once. The order stopped meaning anything the moment
more than one request was waiting.

### Fixed

| Issue | Fix |
|---|---|
| Ordering by `created_at` alone left same-second arrivals to the query planner — and `created_at` is stored to the second, so ideas typed together always tie. | `ORDER BY created_at, rowid`. `rowid` is the insertion counter, so the order is total and written down rather than inherited from an index the optimizer may stop using. |
| Up to five requests started per pass, all running at once. | `start_queued` starts one and stops. `in_flight()` holds the queue while a run is moving; the next request starts when it finishes. |
| A request that failed to start had its place taken by a newer one on the first bad minute. | It keeps the front of the queue for all three of its chances. Only once they are spent is it marked with the reason and the next promoted. |

**Decision taken.** A run **waiting on a person does not hold the queue**. Every outward
action parks at a human gate, so waiting is this company's resting state, not an exception —
holding all new work behind one unread approval would stop the company rather than pace it.
`Stalled` does hold: the sweep still tries to drive it and it may yet finish.

**Ordering trap avoided.** The hold is checked *inside* the loop, after the adopt branch.
An orphaned run left by a previous attempt *is* the head request's own run; a hold placed
before the loop blocked the one piece of bookkeeping that reconciles it. The existing
adoption test caught this.

### Fallout the serial queue caused, and why the test changed

`test_a_suspended_organization_starts_nothing_drives_nothing_and_still_watches` failed on
the first full run. Not a broken test — the new rule working. A run was already moving when
intake fired on resume, so the queue held and the freshly-fired schedule started nothing
that pass.

The test's real subject is that suspension does not *lose* a due schedule, and that still
holds: `fire_due_schedules` runs before the hold, so the goal is queued the moment the pause
lifts. Only the start is deferred. The test now asserts both halves — the schedule fires
into the queue on resume, and starts on the following pass once the moving run finishes —
which covers more than it did before.

---

## Stage 2 — Planning a queued idea

**Path.** `start_queued` → `planner.plan(goal, run_id, backend, costs)` → up to 3 attempts of
`backend(request)` → `extract_json` → force the id → `enforce_budget` →
`core.validate_workflow`, whose errors are handed straight back as repair feedback → write
`runs/<run_id>.planned.json` → `core.create_run` with the planning spend seeded.

**Inputs.** One goal string, a run id, a planner backend.
**Outputs.** A validated workflow, a run on disk, `planning_spend_usd` charged to it.
**Decision points.** Valid JSON · schema validation · repair or give up after 3 · transient
failures raise instead of spending the repair budget.
**Handoff.** An `active` run with every step `pending` or `ready`.

### What the evidence said

Reading the runs on disk rather than the code first changed the finding. Every large
generated workflow is dead:

| steps | completed | died of |
|---:|---:|---|
| 22–26 | 0–1 | `blocked_retry_limit` / `blocked_review_limit` |

Six of six. Small runs (1–6 steps) mostly completed. None ran out of *cycles* — measured
2.0–3.0 cycles per step against a budget of 4, so `CYCLES_PER_STEP` is sound.

Opening two of them: the first research step failed its acceptance criteria three times and
took all 25 pending steps with it. Four of the seven dead steps sat at
`max_attempts == max_review_cycles + 1`.

### Fixed

`planner.py` had been *advising* `max_attempts = max_review_cycles + 2` in prose, and warning
about this exact failure, while `validate_workflow` enforced only `+ 1`. Models wrote the
schema minimum, not the advice. **Advice in a prompt is not a rule; the validator is.**

- `validate_workflow` now enforces `max_attempts >= max_review_cycles + 2`, and the refusal
  names the rule, because that string becomes the planner's repair feedback.
- The prompt quotes the same number the validator refuses on — a plan written to a rule the
  runtime does not hold, or a runtime holding one the prompt never states, wastes a repair
  round either way.
- The 7 shipped steps below the floor were migrated (`fix-onboarding.json` ×6,
  `maker-checker-gold-run.json` ×1), and a test now asserts every shipped workflow validates,
  so the files and the validator cannot drift apart again.

**A reversed decision, recorded.** `test_the_runtime_floor_stays_where_it_is` previously
asserted the opposite, reasoning that raising the floor "would invalidate every shipped
workflow, which is a migration, not a fix." That was true and the migration is now done. The
replacement test carries the reversal and the evidence for it.

### Known and accepted

- **`enforce_budget` cannot honour its own promise past 25 steps.** It sets
  `max_cycles = min(len(steps) * 4, 100)`, so a 30-step plan gets 3.3 cycles per step, and
  the human budget extension is capped at 100 as well — such a plan can never finish. Not
  what is actually killing runs today (none came close to the cycle ceiling), so it is
  recorded rather than fixed. Revisit if a plan ever dies at `blocked_cycle_limit`.
- **`extract_json` takes the outermost `{...}` greedily.** Prose containing braces before the
  JSON costs one repair attempt, then self-heals through the feedback loop.

### Carried forward

| ID | Issue | Revisit at |
|---|---|---|
| C-3 | **The run records the model's goal, not the person's.** `plan()` forces `workflow["id"]` but not `workflow["goal"]`, so `run.created` stores whatever the model wrote. The Ideas panel shows the operator's words and the Runs list shows the paraphrase — and the human at an approval gate sees the paraphrase, not the request. One line to fix; held for Stage 3, where what a human is shown before approving is the subject. | Stage 3 |
