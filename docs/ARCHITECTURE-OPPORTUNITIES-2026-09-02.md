# Architecture Review — cycle 3

**2026-09-02. Analysis and planning only; nothing here is implemented.**

Method: read the runtime as it actually executes — `scheduler.sweep` → `triggers.intake` →
`executor.advance` → `company_runtime.mutate` — then judge it against what the product claims
to be: a company that acts on plain-language goals while a human holds the pen on anything
outward or irreversible.

Evidence tags: **[V]** verified by executing it · **[V-static]** verified by reading the code
· **[I]** inference · **[?]** unknown.

Companion trackers:
[ARCHITECTURE-OPPORTUNITIES-2026-09-01.md](ARCHITECTURE-OPPORTUNITIES-2026-09-01.md) (cycle 2,
IDs `A-01`…`A-10`) and [AUTONOMY-AUDIT-2026-09-01-REV2.md](AUTONOMY-AUDIT-2026-09-01-REV2.md)
(implementation status). This cycle uses `B-` IDs so nothing collides.

---

## 1. Current architecture relevant to harnesses, loops, hooks and control mechanisms

What already exists, and whether it works. This section is deliberately as long as the
proposals, because the strongest finding of this review is that **most of what a reviewer
would reflexively propose is already here**.

### 1.1 The loops

| Loop | Where | Bound | Verdict |
|---|---|---|---|
| **Execution** | `executor.advance` | `MAX_ITERATIONS = 50` per call | Working. Drives ready + `in_progress` + `awaiting_check` steps, skips anything another holder claims, returns on terminal or nothing-to-do |
| **Supervisory** | `scheduler.serve` → `sweep` | `interval=60s`; `max_passes=0` supervised | Working. `single_instance` file lock stops a second sweeper; `Shutdown` finishes the pass it is in rather than orphaning a claim |
| **Intake** | `scheduler.intake` → `triggers.fire_due_schedules` + `start_queued` | `limit=5`/pass, `MAX_QUEUED_TRIGGERS=50`, `MAX_TRIGGER_ATTEMPTS=3` | Working. Wrapped so a planning failure never stops the drive pass |
| **Quality** | `prompts.structural_failure` → `executor.acceptance_failure` → `checking.drive_check` | `GRADE_ATTEMPTS=3`, retry/review limits | Working. An unreadable verdict is a RETURN; an unreachable grader **holds for a human** rather than passing |
| **Cost** | `executor.over_budget` → `hold` | `MYORG_RUN_CEILING_USD`, default `$5` | Working, **per run only** — see B-04/B-05 |
| **Cycle** | `mutate` → `blocked_cycle_limit` → `extend_budget` | `max_cycles`, ≤100 | Working, resumable |
| **Learning** | `checking.propose_lesson` → `memory.propose` → human → `executor.remembered_for` | `MAX_RECALL=5`, `MAX_BODY_CHARS=600` | Working but thin — 2 entries, keyword recall, per-step only (A-04) |
| **Escalation** | `sweep.watch` → `escalation.scan` → `notify.raise_notice` | dedup by `notice_id` | Detection works; **delivery does not** — `MYORG_NOTIFY_COMMAND` is unset, so the outbox is a file (A-02, NOTIFY-01) |
| **Projection** | `sweep.mirror` → `projection.project_all` | one-way, log → SQLite | Working, best-effort, never blocks the driver |

### 1.2 The control mechanisms

- **Gate model.** `policy.json` maps 17 actions to green/yellow/red. `request_step` parks
  yellow at `awaiting_approval` and hands red back at `blocked_human` with **no code path that
  can approve it** [V-static, `company_runtime.py:286`]. This is the constitution, enforced.
- **Audit as a side effect of the gate.** `mutate` writes the audit entry *before* the run
  event, so a log that cannot be written stops the transition. An agent cannot choose not to
  be audited. This is the single best design decision in the codebase.
- **Idempotency.** `mutate` refuses a `request_id` reused for a different mutation and replays
  an identical one. `executor.request_id` derives from (run, step, attempt, verb); `agent_api`
  deliberately uses a *different* scheme so an outside worker's call is never answered with the
  driver's earlier result.
- **Fencing.** Steps carry `claim_token` + `claim_expires_at`; schedules are fenced on
  `next_fire_at` in one UPDATE; the sweeper holds a file lock.
- **Fail-open / fail-closed asymmetry.** `over_budget` fails **open**; the grader fails
  **closed**. Both are documented at the call site with the reasoning. Correct, and the
  documentation is what stops a future reader "fixing" it.
- **Untrusted input containment.** A webhook payload never becomes a prompt: the body selects
  a *pre-registered* trigger by one field, and the goal text comes from the registration a human
  made [V-static, `api.py:165`, `triggers.event_type_of`]. This is the right shape and is worth
  saying out loud, because it is the defence most systems get wrong.

### 1.3 What is already sufficient — do not add to it

- **No message bus, no supervisor agent, no policy DSL, no workflow engine.** Cycle 2 rejected
  all four with reasoning that still holds. This review re-examined them against the current
  code and reaches the same conclusion. A supervisor agent in particular would put a model in
  the control path, correlated with the models it supervises.
- **`pyproject.toml` declares zero dependencies and a test enforces it.** Every proposal below
  is checked against that constraint. None of them needs a dependency.

---

## 2. Key opportunities discovered

Eight candidates. Five survive scrutiny; three are rejected or folded in §9.

### B-01 — One liveness record, not two (`leases` vs `claims`)

**Problem.** There are two independent liveness mechanisms for the same step, and they do not
know about each other.

- `company_runtime` mints a **claim**: `claim_token` + `claim_expires_at`, `CLAIM_SECONDS=600`.
- `leases.py` keeps a separate **lease** in `runtime/runs/leases.json`, `LEASE_SECONDS=600`,
  renewable.

`agent_api.claim` takes *both* — `core.request_step(holder="api-<agent>")` and then
`leases.grant` [V-static, `agent_api.py:107-137`]. But `agent_api.heartbeat` renews **only the
lease** [V-static, `agent_api.py:187-194`]. The runtime claim is never extended.

**Consequence.** An external worker that heartbeats faithfully still loses its runtime claim
after 600 s. `executor.drive_step` then sees `not claim_is_live(step)`, calls `take()`, and
dispatches the same step in-process while the worker is still working [V-static,
`executor.py:349-360`]. Two agents, one step, two bills. Worse: when the worker finally posts
`/v1/submit`, it reads `state["steps"][step_id]["claim_token"]` **at submit time** — which is
now the executor's token — and passes the claim check, completing a step the driver is
concurrently working on [V-static, `agent_api.py:170-178`].

**Why the existing architecture does not cover it.** Each mechanism is individually correct.
The defect is the seam. `sweep` calls `leases.reclaim()` every pass, so the lease side is
live code, not dead code — it is wired in but not authoritative.

**Severity, honestly.** Latent. REV2 records that no lease file exists outside tests and no
external worker runs today, so this has never fired. It becomes a live P0 the day the
agent-facing API is used for real, which is exactly the direction the product is heading.

**Proposed mechanism.** Delete the lease store; make the runtime claim the single liveness
record. `heartbeat` becomes a `renew-claim` mutation that extends `claim_expires_at`;
`reclaim` becomes `expire-claim` on stale holders. One concept, one expiry, one audit trail —
and a smaller codebase than today.

**Simpler alternative considered.** Make `heartbeat` renew both. Rejected: it leaves two
clocks that must be kept in step forever, and the next person to touch either one re-opens
the same gap. The root-cause fix is the smaller diff.

**Priority: P1 now, P0 before any external worker is admitted.**

### B-02 — A human stop on a run in flight

**Problem.** There is no `cancel`, no `abort`, no `stop` anywhere in the runtime or the UI
[V — grep across `runtime/*.py` and `apps/control-center/` returns only `projection.py`'s
`"rejected" → "cancelled"` label mapping]. The full CLI verb list is: `validate`,
`create-run`, `request-step`, `fail`, `complete`, `hold`, `take`, `approve`, `reject`,
`send-message`, `check-*`, `expire-claim`, `extend-budget`, `status` [V-static,
`company_runtime.parser()`].

The only human stop lever is `reject`, and it works **only on a step that is currently
`awaiting_approval`** [V-static, `company_runtime.py:320-325`]. Five of the seventeen policy
actions are green — `research`, `analyze`, `draft`, `validate`, `internal_write` — and they
are the most common. **A run composed only of green steps never parks, and therefore cannot
be stopped by a human at all** until it finishes, exhausts its cycles, or hits its cost
ceiling.

**Why this matters more than it sounds.** The constitution says humans hold the pen. In
practice the human holds the pen on *outward* actions and has no pen at all on *the run*. A
mis-planned run started by a 3am webhook is watched, alerted on, and un-stoppable except by
killing the daemon — which also kills the approval console the operator would use to fix it.

**Proposed mechanism.** `cancel-run <run_id> --approver --reason --request-id`: a `mutate`
that sets `run_status="cancelled"`, adds it to `TERMINAL_RUN`, writes a yellow audit entry
with the approver, and keeps every artifact. Plus the button in the Control Center. It is a
new terminal state on a state machine that already has six, using the same transition path as
everything else.

**Interaction.** `mutate` already refuses a terminal run, so an in-flight dispatch that
returns after the cancel fails its `complete` with `SystemExit` → `ExecutorError` → caught by
`sweep`. The work is paid for and discarded. That is the correct behaviour and must be proven
by test, not assumed (§6).

**Priority: P0 among this cycle.**

### B-03 — A company-wide pause (drain)

**Problem.** The same gap one level up. To stop *everything* an operator must `SIGTERM` the
scheduler, which needs shell access on the host and takes down the whole service. There is no
"stop starting new work, finish what is in flight."

**Evidence [V-static].** `serve()` loops unconditionally; `sweep()` has no admission check;
the only related control is the Control Center's per-schedule enable toggle
(`setScheduleEnabled`), which does not stop webhooks and does not stop runs already moving.

**Proposed mechanism.** One flag the sweep reads at the top of each pass — a file next to
`_scheduler.lock`, or a row the admin CLI sets. Paused = skip `intake()`, keep driving what
exists (or skip both, operator's choice via two levels: `pause-intake` / `pause-all`). Five
lines in `sweep`, one CLI verb, one UI switch.

**Why not just stop the daemon.** Because the daemon is also what escalates, projects, and
reclaims. Stopping it makes the company silent, which is the failure mode the whole
observability layer exists to prevent.

**Priority: P1. Ships with B-02 — same surface, same audit shape, same reviewer.**

### B-04 — Spend that happens before a run exists

**Problem.** `triggers.start_queued` calls `planner.plan()`, which is 1 model call plus up to
`MAX_REPAIR_ATTEMPTS=3` repairs [V-static, `planner.py:24,145`]. That spend is charged to
nothing. `charge()` only writes `spend_usd` onto a step and its run [V-static,
`company_runtime.py:412-428`]; `observability._spend` sums `spend_usd` across run logs
[V-static, `observability.py:170-187`]. **The plan is bought before the run exists, so it is
invisible to the gauge and to the ceiling.**

It compounds: a trigger whose planning fails is re-queued and retried up to
`MAX_TRIGGER_ATTEMPTS=3` [V-static, `triggers.py:151-190`], so one bad trigger can buy twelve
planner calls. With `MAX_QUEUED_TRIGGERS=50`, a backlog of malformed triggers is a few hundred
uncounted, uncapped model calls — at the measured cold-call cost from cycle 2 (§A-10), that is
real money spent while nobody is looking.

**Proposed mechanism.** `create_run` accepts the planning cost and seeds `spend_usd` with it.
The plan file is already written next to the run (`<run>.planned.json`), so the association
exists; only the number is missing. `ClaudeCliBackend` already returns `cost_usd` on its
`Output`, so `plan()` need only return it.

**Value.** Makes the A-01 ceiling honest. Today a run's recorded cost excludes the most
expensive single call in its life.

**Priority: P1. Cheap, additive, no new failure mode.**

### B-05 — A fleet-level view of spend (alert first, ceiling only if proven necessary)

**Problem.** The ceiling is per run. Nothing bounds *runs × ceiling*. A schedule firing hourly
overnight creates a run per hour, each entitled to `$5`.

**Evidence [V-static].** `observability` computes `spend_usd_total` and `spend_usd_worst_run`;
`deploy/prometheus-alerts.yml` alerts on `myorg_spend_usd_worst_run > 3` and **on nothing
total**. So the fleet number is measured and unwatched.

**Proposed mechanism, first rung only.** Add one alert rule on `myorg_spend_usd_total`. Zero
code, zero new failure mode, and it produces the evidence that would justify — or refute —
building enforcement.

**Do not build the org/day ceiling yet.** An enforcing org-level gate is a new global control
in front of every dispatch. `over_budget` had to be argued into failing *open* for exactly this
reason; a fleet gate multiplies that risk across every run at once. Build the alert, watch it
for a real week, and only then decide.

**Priority: P1 for the alert. Enforcement: not yet justified.**

### B-06 — A wall-clock deadline on a sweep pass

**Problem.** `sweep` drives runs serially [V-static, `scheduler.py:110-119`]. One `advance()`
can do up to 50 iterations, each dispatching a step with `STEP_TIMEOUT_SECONDS=300`. There is
no per-pass deadline, so one pathological run can hold the pass for hours — during which
`intake()` does not run, no other run moves, and `mirror`/`watch` do not fire. Head-of-line
blocking on the one loop that keeps the company alive.

**Simpler alternative considered and preferred.** Not parallelism — a deadline. After N
seconds, stop starting *new* runs in this pass, log which were skipped, return. The next pass
picks them up. Parallelism would mean concurrent writers to the run log and would put the
`single_instance` reasoning back on the table.

**Priority: P2.** Real, but today the company runs few concurrent runs. Revisit when the run
count justifies it, or bundle with B-03 (same function, same test file).

---

## 3. Independent assessment of necessity and value

| ID | Verdict | Why |
|---|---|---|
| **B-02** | **Essential** | The product's central claim is human control. A green-only run is uninterruptible. That is a hole in the constitution, not a missing feature |
| **B-01** | **Essential, latent** | A correctness defect with a money consequence. Cheap now, expensive to discover in production |
| **B-04** | **Strongly beneficial** | Makes an existing control honest. Without it the ceiling under-reads by the largest single call |
| **B-03** | **Strongly beneficial** | Completes B-02 one level up; five lines |
| **B-05 (alert)** | **Strongly beneficial** | The number already exists and nobody watches it |
| **B-05 (enforcement)** | **Not yet justified** | Needs evidence the alert is insufficient. Adds a global failure mode |
| **B-06** | **Useful, non-critical** | Real head-of-line risk, no current load to trigger it |
| **B-07** (surface `SweepResult.failed`) | **Duplicative** | Already covered: a run that errors without mutating state goes STALLED at 30 min and escalates |
| Org/day enforcing ceiling | **Premature** | See B-05 |
| Parallel sweep | **Too risky for its value** | Reintroduces concurrent writers the design deliberately avoided |

**The pattern worth naming.** Cycle 2 built every mechanism that stops the *machine* from
hurting itself — cost, cycles, retries, replay, fencing. What is missing is the mechanism that
lets the *human* stop the machine. Both stop controls (B-02, B-03) are that one gap at two
scales, and they are small precisely because the state machine and the audit path they need
already exist.

---

## 4. Recommended architecture for the strongest opportunities

### Stop controls (B-02 + B-03) — one project

```
  Operator (Control Center / CLI)
        │
        ├── cancel-run ──► company_runtime.mutate
        │                    ├─ run_status = "cancelled"  (added to TERMINAL_RUN)
        │                    ├─ audit entry: yellow, approval=granted, actor=the human
        │                    └─ artifacts and evidence untouched
        │
        └── pause / resume ──► a flag next to runtime/runs/_scheduler.lock
                                 │
                        scheduler.sweep reads it at the top of the pass
                                 ├─ paused: skip intake(); optionally skip drive
                                 └─ mirror() and watch() always still run
```

**Where it sits in the lifecycle.** `cancel-run` is a terminal transition like the five that
exist; it needs nothing new from the state machine. Pause sits *before* `intake()`, which is
the only place new work enters.

**What precedes it:** nothing. **What depends on it:** every future increase in autonomy —
more triggers, more schedules, live connectors — is safer to turn on once there is a stop.

**How success is determined:** a run in any non-terminal state can be stopped by a named human
in one action, is recorded as stopped by that human, and never moves again.

**When it cannot proceed:** cancelling an already-terminal run is a no-op that says so
(`mutate` already refuses terminal runs; the message must be legible, per REC-11's lesson).

**Recovery:** there is none, by design — cancel is terminal. Restarting the work means a new
run, which keeps the audit chain honest about what was abandoned and what was re-attempted.

### One liveness record (B-01)

```
  before:  claim_token/claim_expires_at (runtime)  ⟂  leases.json (agent API)
  after:   claim_token/claim_expires_at            ← heartbeat renews this
           leases.py deleted; sweep.reclaim() → expire-claim on stale holders
```

Strictly a deletion plus a rename of one call site. The audit trail improves: claim renewal
becomes a run event, which today it is not.

### Planning cost (B-04)

`plan()` returns `(workflow, cost)`; `start_queued` passes the cost to `create_run`;
`create_run` seeds `state["spend_usd"]`. Three lines, one new column in a dict that already
exists.

---

## 5. Risks, conflicts, and regression concerns

| Risk | Proposal | Severity | Mitigation |
|---|---|---|---|
| Cancel races an in-flight dispatch; the subprocess returns after the run is terminal | B-02 | **Medium — must be tested** | `mutate` already refuses terminal runs; the `complete` fails, `sweep` catches. Prove it, and prove the evidence file is kept |
| Cancel becomes an agent-reachable action | B-02 | **High** | Same shape as `approve`: requires a named approver, writes a yellow audit entry, and is never in `tools.json`. An agent must not be able to cancel its own run to escape a gate |
| Pause is set and forgotten; the company goes quiet and looks healthy | B-03 | **High** | Paused must be a *gauge* (`myorg_paused`) with an alert after N minutes. A silent company is the failure mode the whole observability layer exists to catch |
| Deleting `leases.py` breaks the agent API's contract | B-01 | Medium | `lease_expires_at` in the API response becomes `claim_expires_at`; same field, same meaning. Version the response or keep the key name |
| Removing the lease store loses in-flight liveness during the change | B-01 | Low | No lease file exists today [V, REV2] |
| Seeding `spend_usd` at creation trips the ceiling earlier | B-04 | Medium | Deliberate — the run really did cost that. But it shifts the meaning of the `$5` default; re-derive it from a measured run *including* planning before shipping |
| A fleet ceiling stops the whole company on a metrics glitch | B-05 enforcement | **High** | Reason not to build it yet |
| A pass deadline abandons a run mid-iteration | B-06 | Low | Deadline is checked *between* runs, never inside `advance()` |

**Conflicts.** None between B-01…B-06.
**Overlaps.** B-02 and B-03 share a surface and should ship together. B-06 touches the same
function as B-03 and should not be a separate change to `sweep`.
**Must not coexist.** An enforcing org ceiling (B-05) and the per-run ceiling (A-01) need one
vocabulary and one extension path, or an operator faces two different "you are out of budget"
messages with two different fixes. This is the same trap A-01/A-05 already had to avoid.
**Backward compatibility.** All additive except B-01, which changes one API response field.
`cancelled` is a new terminal status the projection must map (`COARSE_STATUS`) or the Control
Center will show it as unknown.

---

## 6. Validation required before implementation

**B-02 — cancel.** Four things must be true, each a test:
1. A run cancelled while a step is `in_progress` with a live claim reaches `cancelled` and
   never moves again.
2. A `complete` arriving *after* the cancel is refused, the executor survives it, and the
   sweep continues to other runs.
3. Every artifact and evidence file written before the cancel still exists and still hashes
   to what the log records. **Cancel must not be a delete.**
4. The audit entry names the human. No code path cancels a run without an approver — the same
   invariant `extend_budget` already enforces for cycle budgets.

**Invariant to protect:** the hash chain. A cancel is one more event on it, not a truncation.

**B-03 — pause.** Prove that a paused sweeper still projects, still escalates, and still
exposes a gauge saying it is paused. A pause that also silences the watchers is worse than no
pause.

**B-01 — one liveness record.** Before deleting anything, write the failing test that proves
the current defect: claim a step through `/v1/claim`, heartbeat past `CLAIM_SECONDS`, then run
a sweep, and assert the executor does **not** take the step. It should fail today. If it
passes, this finding is wrong and the proposal is withdrawn.

**B-04 — planning cost.** Measure one real planning call and one repair cycle so the seeded
figure comes from evidence. Then re-derive the `$5` default: if planning is ~$1 of a ~$5 run,
the ceiling is materially tighter than the number cycle 2 chose, and the default must move
rather than silently parking more runs.

**B-05 — alert.** No validation beyond a week of data. That is the point of doing it first.

**What would cause rejection.** B-01 is withdrawn if the failing test passes. B-02 is redesigned
if cancelling proves to require touching `advance()`'s inner loop — that would mean the state
machine, not the verb, is the wrong shape, and the design should change rather than the loop.

---

## 7. Architecture opportunity tracker (cycle 3)

An entry graduates to implementation only when its §6 validation has passed.

| ID | Mechanism | Problem | Evidence | Value | Depends on | Key risk | Validation gate | Pri | Status |
|---|---|---|---|---|---|---|---|---|---|
| **B-02** | `cancel-run` — a human stop on a run | Only stop lever is `reject`, and only on a parked yellow step; a green-only run is uninterruptible | No cancel verb anywhere **[V]**; `reject` requires `awaiting_approval` **[V-static]** | Closes the hole in the human-control claim; unblocks every later autonomy increase | — | Must not become agent-reachable; must not delete evidence | 4 tests in §6 | **P0** | Proposed |
| **B-01** | One liveness record; delete `leases.py` | `heartbeat` renews the lease, not the claim → executor takes over a live external worker after 600 s | `agent_api.py:187-194` vs `executor.py:349-360` **[V-static]** | Removes duplicate execution and double spend; net deletion | — | API field rename | The failing test must fail first | **P1** (P0 before external workers) | Proposed |
| **B-04** | Charge planning cost to the run | Plan is bought before the run exists; invisible to gauge and ceiling | `planner.plan` 1+3 calls, `charge()` is step-scoped **[V-static]** | Makes A-01's ceiling honest | — | Shifts the meaning of the `$5` default | Measure a real plan, re-derive the default | **P1** | Proposed |
| **B-03** | Pause / drain switch | Only company-wide stop is SIGTERM, which needs shell access and silences the watchers too | `sweep()` has no admission check **[V-static]** | Operator can stop the bleeding from the console | B-02 (same surface) | A forgotten pause looks like a healthy quiet company | Paused sweeper still projects, escalates, and reports a gauge | **P1** | Proposed |
| **B-05a** | Alert on `myorg_spend_usd_total` | Fleet spend is measured and unwatched | `observability.py:186` vs `prometheus-alerts.yml` **[V-static]** | Zero code; produces the evidence for B-05b | — | None | — | **P1** | Proposed — do first |
| **B-05b** | Enforcing org/day ceiling | Nothing bounds runs × ceiling | Inference from B-05a **[I]** | Would cap the fleet | B-05a, A-01 | A global gate that can stop the whole company | A week of B-05a data showing the alert is insufficient | P3 | **Not yet justified** |
| **B-06** | Wall-clock deadline on a sweep pass | One slow run blocks intake and every other run | `sweep` is serial, no deadline **[V-static]** | Removes head-of-line blocking | B-03 (same function) | Deadline inside `advance()` would abandon work | Deadline checked between runs only | P2 | Deferred |
| **B-07** | Surface `SweepResult.failed` | Repeated per-pass errors are logged and dropped | `scheduler.py:117` **[V-static]** | — | — | — | — | — | **Rejected — duplicative** (STALLED at 30 min covers it) |

Cycle-2 entries `A-02`, `A-03`, `A-04`, `A-07`, `A-08` remain open in their own tracker and are
not restated here.

---

## 8. Dependencies and suggested future implementation order

```
  B-05a (alert on total spend)  ── no dependencies, no code ── DO FIRST
       │
       └──► a week of evidence ──► B-05b decision (build or drop)

  B-02 (cancel-run) ──┬── B-03 (pause/drain)   same surface, same tests, one project
                      └── Control Center: run list + cancel button
                              │
                              └──► B-06 (pass deadline)  same function as B-03

  B-01 (one liveness record) ── independent ── must land before any external worker

  B-04 (charge planning cost) ── independent ── then re-derive A-01's default
```

**Leverage.** B-02 built as a proper terminal transition gives B-03 its vocabulary for free,
and gives the Control Center the run-detail view REV2 lists as missing. One project, three
gaps closed.

**Independent and parallelisable:** B-05a, B-01, B-04.
**Should not be separate changes:** B-02/B-03, and B-03/B-06 both touch `sweep`.
**Should not be built at all yet:** B-05b.

---

## 9. Rejected, deferred, or needing more evidence

**Rejected outright:**

- **Surfacing `SweepResult.failed` as its own notice (B-07).** A run erroring every pass does
  not mutate state, so `updated_at` stops moving, `health.classify` marks it STALLED at 30
  minutes, and `escalation.scan` raises `RUN_STALLED`. Adding a second path to the same notice
  would produce two alerts for one problem.
- **A parallel sweep.** Concurrency here means concurrent writers to the run log. The whole
  `single_instance` argument exists to avoid that. B-06's deadline gets most of the benefit for
  none of the risk.
- **A retry/backoff layer on failing runs.** Tempting, and unnecessary: the cost ceiling already
  bounds what a retry loop can spend, and the retry/review limits already bound how many times a
  step is attempted. A backoff layer would add a clock to a system that is already bounded by
  money and by count.
- **Re-litigating cycle 2's four rejections** (message bus, supervisor agent, policy DSL,
  workflow engine). Re-examined against the current code; all four still correct.

**Deferred, evidence needed:**

- **B-05b, the enforcing fleet ceiling.** Needs a week of B-05a.
- **B-06, the pass deadline.** Needs a run count that makes head-of-line blocking observable.
- **Per-agent sandboxing beyond the workspace, and multi-host coordination.** Unchanged from
  cycle 2: both belong to the Docker/VPS migration, not before it.

**Noted, not proposed.** `over_budget` re-reads the whole run log once per ready step per pass
[V-static, `executor.py:321`]. Deliberate and correct — a stale ceiling is not a ceiling — but
it is O(steps × log size) per pass and will show up first as sweep latency. Watch it; do not
optimise it now.

---

## 10. Best candidate to validate first

**B-02, the cancel verb — with B-05a's alert added in the same sitting because it costs
nothing.**

**Why it is the strongest next step.** Every control this product has built stops the machine
from hurting *itself*: cost ceilings, cycle caps, retry limits, replay protection, claim
fencing. Cycle 2 finished that work well. What no control does is let a *person* stop the
machine. The gate model gives the human a veto at yellow and red actions, and that veto is
real — but it only fires where the plan happens to have put a gate. A run of five green steps
never asks anyone anything, and there is no verb, no button, and no API call that ends it. The
one lever that exists, `reject`, requires the run to already be waiting.

That is a gap in the constitution, not in the feature set. It is also the prerequisite for
every autonomy increase still on the roadmap: more triggers, more schedules, live connectors,
external workers. Each of those makes it more likely something starts that a person wants
stopped, and none of them should be turned up while the answer to "stop it" is "ssh in and
kill the daemon."

**What must be proven before implementation.** One question first, and it is a ten-minute
experiment: **what happens to an in-flight dispatch when its run goes terminal underneath it?**
Cancel a run while a step is `in_progress`, let the subprocess return, and watch. The expected
path is that `complete` hits `mutate`'s terminal-run refusal, raises `SystemExit`, becomes an
`ExecutorError`, and is caught by `sweep` — the work is paid for and discarded. If that is what
happens, the verb is a small change to a state machine that already knows how to end. If
instead the failure escapes the sweep or corrupts the pass, the design must change before a
line of it is written. Do not design further until that is run.

Then three more tests, from §6: evidence survives a cancel intact and still hashes true; the
audit entry names a human; and no code path cancels without an approver.

**What existing behaviour must be protected.**

- **The hash chain.** Cancel is one more event appended, never a truncation, never a deletion.
  Everything the run produced before the stop stays exactly where it is and stays verifiable.
- **The red-step invariant.** `request_step` has no code path that approves red. Cancel must not
  become one — it ends a run, it never performs the action the run was blocked on.
- **Agent unreachability.** Cancel belongs with `approve` and `extend_budget`: a human verb with
  a named approver and a yellow audit entry, never in `tools.json`, never something an agent can
  call to escape its own gate.
- **`mutate`'s terminal refusal.** It is what makes the whole thing safe. Do not add a bypass;
  `extend_budget` needed one and paid for it by hand-rolling its own replay check. Cancel needs
  no such exception — it moves *into* terminal, not out of it.

**What would make it safe to move forward.** The race experiment run and its outcome recorded;
the four tests written and passing; `cancelled` mapped in `projection.COARSE_STATUS` so the
Control Center does not show an unknown state; and one end-to-end pass where an operator stops
a real run from the console and the audit log tells the whole story afterwards — who stopped
it, when, why, and what it had already produced.
