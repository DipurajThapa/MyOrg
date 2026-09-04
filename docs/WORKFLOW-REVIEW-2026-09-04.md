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

---

## Stage 3 — Execution and the approval gate

**Path.** `advance()` drives every `ready` step (independent steps together, since `ready`
implies independent) → `request_step` parks yellow at `awaiting_approval` and stops red at
`blocked_human` → `approvals.pending()` gathers what a person needs → `approve`/`reject`
through the runtime's own verbs, each writing its audit entry as a side effect of the
mutation.

**What holds.** A red step is unapprovable by any code path — `approve` refuses anything not
`awaiting_approval`, and red never reaches that status. Approval requires a reference.
Waiting for a person never spends an attempt. A cancelled run discards work in flight rather
than grading it. Org scoping means another company's decision is invisible, not forbidden.

### Fixed

| Issue | Fix |
|---|---|
| **The run recorded the model's goal, not the person's.** `plan()` forced `workflow["id"]` but not `workflow["goal"]`, so a paraphrase became the run record, the runs list, and the goal a human reads on the screen where they approve an outward action. The idea typed and the run it became stopped saying the same thing. | One line, for the same reason the id is forced: the caller owns it. |
| **`waiting_since` was the run's last-event time, not the step's park time.** Steps ready together are driven together, so a run with a gate parked and another branch still working refreshed the gate's own waiting time every pass — and the gauge for "longest anything has waited on a person" reset with it. Mutation check: a gate parked at 10:00 reported 10:05, the moment an unrelated branch moved. | `parked_at()` walks back through the events while the step was still waiting. |
| **C-1 / C-2: a queued request could not be withdrawn.** With the serial queue this became a dead end rather than an annoyance — a transient failure spends no attempt, so an unreachable planner held the front of the line for ever and nothing behind it started, with nothing a person could do. | Migration 007 adds a `withdrawn` status, plus a service method, a route, and a console button offered only while the request is still queued. |

**Why `withdrawn` is not a reuse of `failed`.** `failed` means the company tried and could
not, and `escalate_ideas` raises a notice for every failed row — recording a person's own
withdrawal there would have told them their request "could not be planned". The same status
list also keeps it off the operator's screen, where it would otherwise sit looking unfinished.

**Every fix was mutation-checked**: the fix is reverted, the test is confirmed to fail, and
the fix restored. A test that cannot fail is not evidence.

### Known and accepted

- **Rejecting one step ends the whole run.** `reject` sets `run_status="rejected"`, so in a
  plan with two independent gates, refusing one kills the other branch's finished work. The
  planner is told to put the gated step last and there is normally one, so this is recorded
  rather than changed — revisit if multi-gate plans become common.
- **A withdrawal is recorded in `last_error` ("withdrawn by X: reason") rather than as an
  operational event.** `settle_trigger` takes no actor, and widening it for this alone was
  not worth it. The row keeps who and why.

---

## Stage 4 — The outward action executing

**Path.** An agent proposes → `request_approval` hashes (connector, action, target, payload
ref, payload sha) into one `action_hash` → a human decides with that exact hash →
`live_gateway.execute` re-verifies the payload bytes against the hash, consumes the approval
**atomically with writing the receipt, before a single byte leaves**, sends, settles.

**What holds, and this layer is the strongest in the system.** The approval is single-use and
bound to one exact call, so a different payload cannot reuse it. The requester cannot approve
its own action. Expiry is checked inside the consuming transaction, not before it. The host
allowlist is re-checked at send time in case a `base_url` drifted. A timeout settles as
`in_flight` and is never auto-retried — a person reconciles it, because "we do not know" is
the only honest answer to a timeout.

### Fixed

| Issue | Fix |
|---|---|
| **The gate could not be answered.** Two routes existed — create and decide — and nothing in between. No route and no store query ever reported that an approval was waiting, and the console had no panel for it. Answering one meant already knowing its id *and* its 64-character action hash. The strictest gate in the company was unanswerable in practice. | `pending_approvals` in the store, `GET /v1/approvals`, and a console panel that sends the hash back verbatim rather than asking anyone to read it. |
| **Fifteen minutes to decide.** `request_approval` gave a *human* a machine's deadline, in a system whose whole premise is that people answer when they get to it and whose notification path exists because nobody watches the screen. Nothing renewed it; the agent had to propose again. | `APPROVAL_WINDOW_HOURS = 24`, named and reasoned. The approval binds one exact payload by hash, so a longer window lets nothing else through — it only lets a person answer at a realistic hour. |
| **The one gate that actually sends recorded no why.** Every other human decision here carries a 1..200 character reason that becomes the approval reference. This one took only a decision and a hash. | A reason is required and recorded in the `approval.approved` / `approval.rejected` event. It rides in the event payload rather than a new column, so no migration. |

### Also removed

- **`Store.consume_approval` (db_runs.py) is gone.** Both gateways use
  `consume_approval_and_record_receipt`; nothing called this one. It was a second,
  unexercised implementation of *spending an approval* — the most security-sensitive
  operation here — where a maintainer could reasonably have fixed one and not the other.
  Flagged first rather than deleted in passing, because removing code on an approval path is
  a decision, not tidying; removed once that decision was taken.

### Known and accepted

- **The step gate and the connector gate are two separate approvals.** `approve` moves a run;
  `decide_approval` unlocks one outward call. The code says so deliberately
  (`service.py`), and `company/operating-principles.md` documents both. Nothing wires one to
  the other, so a real outward action needs two human decisions. Correct but worth knowing.

---

## Stage 5 — Completion, and what the company learns

**Path.** A checker rejection → `checking.propose_lesson` → `memory.propose` (status
`proposed`) → a human keeps or discards it in the console → `memory.recall` pulls approved
entries by keyword overlap → `executor_steps.remembered_for` puts them in the next agent's
dispatch prompt. Terminal runs write themselves to the audit log; evidence stays on disk and
is readable through `run_output`.

**The loop is genuinely wired.** Propose → approve → reuse all exist and connect. Recall is
plain keyword overlap on purpose, so an operator can always see why a memory surfaced, and it
never blocks the work.

### Fixed

**The company could only ever learn one lesson per department-and-action pair.** `propose()`
derived a lesson's identity from its *subject* alone, and the only caller in the runtime
builds every subject as `"{owner} on {action} work"`. So the first rejection from a given
department was kept and every later one was dropped — `propose` returned `None` and
`checking.py` did `if entry:`, so the checker's insight disappeared without even a log line.

Run against the real module, three genuinely different research lessons collapse to one:

```
1st proposal: mem-9ca1678e00
2nd proposal: None   <-- a different lesson, same pair
3rd, after the 1st was approved: None
```

Identity now keys on subject **and** body, so different lessons coexist while the identical
one is still refused. The subject names the step as well, because several proposals from one
department are otherwise indistinguishable in the queue a person reads. And "we already know
this" is logged rather than silent.

Mutation-checked: reverting the fix fails the new test with *"unexpectedly None: a second
lesson from the same pair must not vanish"*.

### Raised for a decision

- **A completed run tells nobody.** `escalate_run` raises a notice for `FAILED`, `WAITING`
  and `STALLED`; `FINISHED` raises nothing and there is no `RUN_COMPLETED` kind. Defensible
  under exception-based autonomy — no news is good news — but two things cut against it: the
  operator *is* told when their idea fails (`IDEA_FAILED`), so the asymmetry is odd, and
  `LESSON_PROPOSED` already notifies about a good thing that needs attention. As it stands,
  somebody who asked for work has to keep opening the console to find out it is ready.

### Known and accepted

- **Lessons only come from checker rejections.** A step with no checker, and a run that
  simply fails, teach the company nothing. Deliberate — a rejection is a clear, attributable
  signal — but it means unchecked work never contributes.
- **`EVIDENCE_DIR` is the repository tree, not `MYORG_RUNS_DIR`.** Already commented in the
  code and in a test's setup; evidence must resolve under the repository root to be accepted
  at all.

---

## The work board

`runtime/kanban.html`, served at `/kanban` (and `/board`) by the same route that serves the
console — same named actor, same loopback rule, same short-lived token, same content policy.
It is the console's other view, not a second application.

### Columns are real statuses, not invented ones

| Column | What lands there | Where the status comes from |
|---|---|---|
| Asked | a queued request | `GET /v1/ideas`, `status=queued` |
| Planning | being turned into a workflow | `GET /v1/ideas`, `status=started` |
| Working | a run the agents are moving | `GET /v1/runs`, `runtime_status=active` |
| Waiting on you | step gates, outward calls, proposed lessons | `/v1/decisions`, `/v1/approvals`, `/v1/memory/proposals` |
| Done | a finished run | `GET /v1/runs`, `runtime_status=completed` |
| Stopped | abandoned requests, blocked/cancelled/rejected runs | `/v1/ideas` `status=failed`; runs where `can_cancel` is false and the run did not complete |

A run whose step is parked appears once, as the decision, not twice.

### Moving a card can never create a state the backend would refuse

Only an action with a real route behind it can be reached by dragging, and each of those
needs a stated reason — so a drop **arms** the card rather than firing it. Dragging is never
itself the decision. Anything else is refused in words: *"Nothing moves an item from 'Asked'
to 'Done'. The runtime moves work itself; a person can only decide, stop or withdraw."*

### What the backend cannot do, said plainly rather than faked

The board offers no control without a route behind it. Where a person would reasonably
expect one, the card says why there isn't:

| Wanted | Status | What the board does instead |
|---|---|---|
| **Retry a stopped run** | No route. `extend-budget` exists as a CLI verb for `blocked_cycle_limit` only and is not exposed over HTTP. | Says *"the workflow needs changing, not repeating"*, and offers **Fix and ask again**, which is a real new request. |
| **Delete a run** | No route, deliberately — the run log is the audit record. | Says so on the card. Deletion is offered only where it exists: **Withdraw**, on a still-queued request, which is confirmed by a required reason. |
| **Edit a queued request** | No route. | **Fix and ask again** prefills the wording, and states that it creates a new request while the original stays for the record. |
| **Name a person who can approve** | No endpoint lists role holders. | Names the required *role*, and whether the signed-in person has it. |
| **Tell someone a run finished** | No `RUN_COMPLETED` notice exists (raised in Stage 5). | The Done column; nothing is pushed. |

### Added to the backend

One read-only route, because the data existed with nothing exposing it:
`GET /v1/runs/{id}/history` returns the run's own append-only timeline — every stage change,
who caused it, when, with attempts, review cycles, approver and stated reason.
`/v1/runs/{id}/events` reads the *store's* operational events, which are empty for a run the
executor drove.

### Checked by looking, not only by asserting

Driven against a seeded runtime with work in every column: a step gate approved end to end
(one `step.approved` event, attributed, with the reason — a deliberate double-click submitted
once), an illegal drag refused, a legal drag armed but not fired, **Fix and ask again**
creating a new request while the failed one stayed on the board, and the action buttons
disappearing with an explanation when the viewer lacks `decision-owner`.

### Answering "why is it stuck", not just "it is stuck"

The first board showed cycles and a generic reason. Cycles say nothing about where the work
is, and `blocked_retry_limit` is a status, not an explanation. Three additions, all surfacing
data the backend already held:

| Added | Where it came from |
|---|---|
| **Step progress and the blocking step** on `/v1/runs` — *"0 of 3 finished, at frame-goal (chief-of-staff, attempt 2 of 2)"* | `run_steps` in the projection, which already stored every step. One query for the whole org, so no extra request per card. |
| **`last_failure`** on each step of `/v1/runs/{id}/output`, plus max attempts, review cycles, checker, dependencies and approver | The run state carried the failure text and nothing exposed it — a step that produced nothing showed an empty box and no reason. |
| **`NEXT_STEP`** in `escalation.py`, beside the existing `DEAD_END` | New, but placed where the notice reads it too, so the board and the inbox cannot tell an operator two different things. Stuck *requests* get the same treatment: a request retrying a busy server is told it needs nothing, which is the opposite advice from one that gave up. |

A stuck run now reads: *"It is sitting on **frame-goal** … attempt 2 of 2"* → *"Every attempt
failed the same way, so repeating it will not help. Read the step's last failure, then ask
again with a narrower goal."* → the grader's actual words → every step, with `waits on
frame-goal` showing what is blocked behind it.

**A product inconsistency this surfaced.** `POST /v1/runs` writes a store row with no runtime
log, so `/output` and `/history` answer 400 for such a run and there is nothing to stop. The
board says so and offers no buttons, rather than three that fail.

### When the page cannot reach the runtime

A console left open reported `GET /v1/me failed (400)` and every panel went blank. The cause
could not be reproduced: token failures answer 401 *with* a message, a query string answers
400 *with* a message, and a token that outlives a runtime restart answers 200, or 401 with a
message when the actor is gone. Nothing this API authors returns a bare status. The likeliest
explanation is that the browser reached port 8080 while the runtime was between restarts.

The cause is unproven; two defects it exposed are not, and both are fixed on the console and
the board alike:

- **A refusal now repeats what the server actually said.** `failed (400)` sent an operator
  hunting through their own code for a fault that was never there. A refusal the API authored
  is quoted; a status with no body says so, and says the runtime always explains itself — so
  the next person knows to look at what is answering on the port, not at the page.
- **A dead token recovers instead of stopping the page.** A token lives ten minutes and the
  renewal is on a timer that a browser may throttle in a background tab. A 401 now fetches a
  fresh token and retries once — once only, because a second refusal is a real one. The
  request id is decided before the first attempt, so a retried write cannot land twice.

---

## Notifications — the part that makes the rest usable

A board only helps somebody who is looking at it. Everything above assumes an operator opens
a screen; this is what happens when they do not.

### What was never raised at all

`escalation.scan` covered runs, memory and requests. It never looked at the connector gate,
so **the two sharpest things in the company were silent**:

| Missing | Why it matters |
|---|---|
| **A proposed outward call** (`call_approval`) | The strictest gate here, and it *expires*. Silence meant the decision was missed rather than delayed — and Stage 4 had already found this gate unreachable on screen, so it was unreachable in both places at once. |
| **A call that left and never settled** (`call_unresolved`) | Nothing may retry it, because nobody knows whether it happened. Only a person can go and look. |

And a finished run raised nothing, which left the odd asymmetry the operator noticed: they
were told when their request died and never when it worked.

### Coverage now

Blocking — a step waiting on a decision · an outward call waiting · a call unresolved · a run
stopped · a request the planner gave up on. Attention — a run gone quiet · a request retrying
too long. Routine — a lesson to keep · **a run that finished**.

Proved against a seeded runtime: six kinds raised, six delivered, blocking first, none
repeated on a second sweep.

### Delivery

The runtime still sends nothing itself. `deploy/notify-email.py` is a second sink beside the
GitHub one, and it exists because the runbook already conceded that the GitHub inbox *"works
and the alert does not"* — GitHub never notifies an account of its own actions. Settings come
from the environment, the recipient included: a personal address is not repository content,
and this repository is public. Only one delivery command runs, so this replaces the GitHub
sink rather than joining it, and the runbook says so.

### A phantom that would have become email

Any `.jsonl` in the runs directory was opened as a run and reported as a **failed** one when
it would not parse. A memory store copied in beside the runs did exactly that, twice, during
this work. Harmless while it was a line in a log; not harmless once those reports are sent to
somebody. `run_files()` now requires the run-id shape, so nothing else is read as a run.

Corrected along the way: I first blamed `shlex` for mangling Windows paths in the delivery
command. It does not — `deliver()` already passes `posix=os.name != "nt"` and says why.
