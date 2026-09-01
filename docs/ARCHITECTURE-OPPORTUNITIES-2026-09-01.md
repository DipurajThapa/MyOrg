# Architecture Review — harnesses, loops, hooks and control mechanisms

**2026-09-01, after cycle 2.** Analysis and planning only; nothing here is implemented.

Method: read the runtime, then judge it against what the product is *for* — a company that
acts on plain-language goals with exception-based human involvement. Evidence tags:
**[V]** verified by executing it here · **[V-static]** verified by reading code ·
**[I]** inference · **[?]** unknown.

Companion trackers: [AUTONOMY-AUDIT-2026-09-01-REV2.md](AUTONOMY-AUDIT-2026-09-01-REV2.md)
(implementation status) and §7 below (architecture opportunities, separate on purpose —
an idea is not a backlog item until its necessity is established).

---

## 1. Current architecture relevant to control mechanisms

Read as five concentric loops. Each is listed with what it actually does, not what it is
named after.

| Loop | Mechanism | State | Verdict |
|---|---|---|---|
| **Intake** | `triggers.py` — signed webhook, schedule fence, replay-safe queue | `trigger_intake`, `schedules` | ✅ Works. Verified end to end **[V]** |
| **Plan** | `planner.py` — goal → DAG, 3 repair attempts against the real validator | `runtime/runs/*.planned.json` | ✅ Works. Not data-informed |
| **Execute** | `executor.advance()` → `drive_step` → backend → grade → evidence → complete | run log (append-only, hash-chained) | ✅ Works **[V]** |
| **Check** | `checking.drive_check()` — a second department, verdict applied by the state machine | run log messages | ✅ Works. Same model family |
| **Gate** | `company_runtime` green/yellow/red; red unapprovable by any code path | run log + `logs/audit-log.jsonl` | ✅ Holds under the driver **[V]** |

**Supporting mechanisms that already exist and work.** Fencing tokens on step ownership
(REC-10) · fail-closed acceptance grading (VAL-07) · hash-chained audit as a *side effect*
of the gate (AUD-01) · one-way projection log→store · notice outbox with dedupe · escalation
scan · propose→approve→recall memory · supervised scheduler with a single-instance lock ·
runtime gauges and five autonomy alerts (OBS-08).

**This is a genuinely good control architecture.** The important structural properties are
already right, and most were bought with small code:

- **Evidence and control are on opposite sides of the trust boundary.** The audited actor
  does not write the audit line.
- **Ownership is a precondition of the write**, not a lease the caller is trusted to check.
- **Controls fail closed.** A grader that cannot run parks the step; an audit log that
  cannot be written stops the gate.
- **Ambiguity is preserved rather than rounded.** `in_flight` is a first-class outcome.
- **The injection boundary is code, not prose.** A webhook payload selects a
  pre-registered goal; it cannot supply one.

The correct posture for this review is therefore **suspicion of new layers**. The gaps below
are mostly *missing feedback*, not missing structure.

### 1.1 What is incomplete, disconnected, or duplicated

Established before proposing anything, as instructed.

| Finding | Evidence | Nature |
|---|---|---|
| **Memory is built and unused.** Full propose→approve→recall lifecycle, hash-chained, org-scoped — 2 entries total | `memory/default.memory.jsonl` **[V]** | Underused, not missing |
| **Org state is dead code.** `scripts/org_state.py` written, `state/` = README only | **[V]** | Duplicated concept — runs already carry goals |
| **Two approval concepts.** `decide_step` (moves a run, run log) and `decide_approval` (governs a connector write, SQLite) | `service.py:94,157` **[V-static]** | Legitimately distinct; **undocumented as distinct**, which is how they get conflated |
| **Two "budget" ideas that do not meet.** `max_cycles` counts *mutations*; nothing counts *money* | `company_runtime.py:248` **[V-static]** | Incomplete — see A-01 |
| **`blocked_cycle_limit` is terminal and unresumable.** Completed work is stranded; re-driving is a silent no-op | REC-11, probe P3 **[V]** | Missing recovery path |
| **Checker and grader share the producer's model family** | VAL-06 **[V-static]** | Correlated blind spot |
| **The escalation loop raises notices nobody is required to answer** | `notify.deliver()` needs an operator-wired command **[V-static]** | Open loop — see A-04 |

---

## 2. Key opportunities discovered

Eleven candidates. Six survive scrutiny; five are rejected or deferred in §9.

### A-01 — A cost ceiling per run and per org

**Problem.** `max_cycles` bounds *state mutations*, not spend. A cycle is one event; a step
is one or more model calls of unbounded size. Nothing anywhere counts tokens or money.
**Evidence [V].** The real-model e2e run: 4-step plan, three attempts at one step, each a
full `claude -p` call — one triggered run, no ceiling on what it could cost. `MAX_QUEUED_TRIGGERS`
caps how many runs *start*; nothing caps what one run spends. `LOOP-06` has been open since
REV2 with "documented caps only".
**Why the existing architecture does not cover it.** The cycle budget is a *deadlock*
guard, not an economic one. A plan with 4 steps and 3 retries each is inside its cycle
budget and can still cost 12 model calls.
**Mechanism.** Record per-step token usage from the backend into the step's state; sum it on
the run; refuse dispatch when a run or org ceiling is crossed, parking at
`awaiting_approval` with the reason — the VAL-07 pattern exactly, reused.
**Value.** This is the one missing control whose absence can cause *unbounded real-world
loss* while every other control reports healthy. Everything else fails safe; this fails
expensive.
**Risk.** Parking on cost could stall a run mid-flight that a human would happily have paid
for. Mitigated by reusing `hold` (work is kept, approval resumes it, the agent never redoes it).
**Simpler alternative considered.** Alert-only on a spend gauge. Rejected: an alert does not
stop the spend, and the failure mode is unattended-at-3am.
**Priority: P0 among these.**

### A-02 — Close the escalation loop with an SLA clock (HOOK-04)

**Problem.** Notices are raised correctly and deduped correctly, and then nothing happens.
`deliver()` needs `MYORG_NOTIFY_COMMAND`; unset, the outbox is a file somebody must open.
**Evidence [V-static, plus probe 2 in REV2 §11.1].** Detection and dedupe verified correct;
`deliver()` returns the same value for "sent 3" and "sent nothing" (NOTIFY-02).
**Why not covered.** OBS-08 now *alerts* on `myorg_approval_wait_seconds_max` — which is a
real improvement and may be sufficient. The remaining gap is that the runtime has no notion
of a *deadline*: the `lead-response` skill's SLA is prose.
**Mechanism.** A due-time on a parked step; the scheduler emits a breach event that escalates
severity and (optionally) re-routes.
**Honest assessment.** OBS-08 covers ~80% of the value. The incremental case for HOOK-04 is
weaker than it looked before OBS-08 landed. **Downgrade to P2** and revisit with evidence
that the alert is insufficient in practice.

### A-03 — Independent, non-model validation for at least one action class (VAL-06)

**Problem.** Structural gate, acceptance grader and maker-checker are all the same model
family as the producer. Three checks, one correlated blind spot.
**Evidence [V].** The real-model run is the *positive* case: the grader caught an empty
deliverable and then caught arithmetic dressed as data. But it caught them because they were
blatant; nothing establishes it catches subtle drift.
**Mechanism.** For one action class where truth is machine-checkable — a produced CSV, a
figure that must reconcile to a source — assert against the data, not against a model.
**Value.** Converts the strongest current claim ("quality is gated") from *probable* to
*verified* for at least one path.
**Dependency.** Needs a real connector (TOOL-04) to have data worth reconciling against.
**Priority: P1, blocked.**

### A-04 — Run-level feedback: a retrospective that reaches the planner

**Problem.** The learning loop is per-step and one-directional. `propose_lesson` fires on a
checker RETURN/REJECT. Nothing summarises a *finished* run, and nothing feeds outcomes back
into planning.
**Evidence [V-static].** `checking.propose_lesson` is the only writer to memory from the
runtime. `planner.plan()` takes `goal` and `feedback` where feedback is *validator errors
from this attempt only* — never "the last three runs of this shape failed at step 3".
**Why it matters.** This is the difference between a company that executes and one that
improves. Today run N+1 is exactly as good as run N.
**Mechanism.** On terminal transition, propose one memory entry naming the run shape and
its outcome; `planner` recalls those for similar goals — reusing the existing
propose→approve→recall path, so nothing reaches a prompt unapproved.
**Risk — the important one.** A feedback loop into planning is where autonomous systems
learn to be confidently wrong. The human approval gate on memory is what makes this safe,
and it must not be relaxed for convenience. Also a poisoning path: a lesson proposed by a
compromised step could bend every later plan. Keep proposals attributable to their source
run, and cap what recall injects.
**Value.** High and compounding. **Priority: P1.**

### A-05 — Make `blocked_cycle_limit` resumable (REC-11)

**Problem.** Budget exhaustion is terminal. Completed steps are stranded and re-driving
returns quietly, so an operator retry looks like success.
**Evidence [V].** Probe P3.
**Mechanism.** A human-approved budget extension, and a terminal re-drive that says so.
Established practice (Temporal, Step Functions treat a budget breach as alarmable and
resumable, not terminal).
**Interaction with A-01.** A-01 introduces a *second* budget with the same shape. Build the
extension path once, generic over both. **This is the leverage point: A-01 and A-05 should
be designed together or A-01 will need its own resume path a month later.**
**Priority: P1, and coupled to A-01.**

### A-06 — Executor mutations are replay-safe (WF-13)

**Problem.** `request_id()` mints a uuid per call, so WF-04's idempotency can never fire on
the autonomous path. A crash-and-resweep re-dispatches and re-pays.
**Evidence [V].** Probe P6: 5 calls → 5 distinct ids.
**Mechanism.** Derive the id from `(run, step, attempt)`.
**Value.** Small change, removes a whole class of double-spend. Directly complements A-01 —
capping spend while leaking spend to re-dispatch is half a control.
**Priority: P1. Cheap.**

---

## 3. Independent assessment of necessity and value

Scored on: does the product's stated purpose fail without it?

| ID | Necessity | Reasoning |
|---|---|---|
| **A-01** cost ceiling | **Essential** | The only uncapped path to real-world loss. Every other control fails safe; this one fails expensive, unattended |
| **A-05** resumable budget | **Strongly beneficial** | Recovery gap today; becomes *essential* the moment A-01 adds a second budget |
| **A-06** replay-safe ids | **Strongly beneficial** | Cheap, closes a known double-spend leak, makes A-01's accounting honest |
| **A-04** run retrospective | **Strongly beneficial** | The difference between executing and improving. Not urgent; compounding |
| **A-03** non-model validation | **Useful, blocked** | Converts a probable claim to a verified one; needs real data first |
| **A-02** SLA clock | **Premature** | OBS-08 took most of its value. Revisit with evidence |

**Deliberately not proposed** (§9 has the reasoning): a workflow-engine rewrite, a message
bus, a policy DSL, per-agent sandboxing beyond the current workspace, and a second
"supervisor agent" layer.

---

## 4. Recommended architecture for the strongest opportunities

One mechanism, not three. **A-01, A-05 and A-06 are the same feature.**

```
dispatch(step)
  ├─ before:  budget.check(run, org)     ── A-01
  │             over ceiling? → hold(step, reason)   [reuses VAL-07's hold]
  ├─ id:      f"exec-{run}-{step}-{attempt}"          ── A-06
  └─ after:   budget.record(run, tokens_in, tokens_out)

blocked_cycle_limit | blocked_budget_limit
  └─ extend-budget --approver --request-id  ── A-05, generic over both budgets
```

Why this shape:

- **It reuses `hold`.** VAL-07 already built "park with a reason, keep the work, approving
  resumes it without re-dispatch". A cost ceiling is the same event with a different cause.
  No new terminal state, no new operator concept.
- **It reuses the audit path.** A budget hold is a gate transition, so it writes its own line.
- **It reuses OBS-08.** Spend becomes another gauge and another alert; no new telemetry.
- **One extension command covers both budgets.** Building A-05 for cycles alone and then
  again for cost is the duplication this review exists to prevent.

**What it must not do:** introduce a budget *service*, a pricing table, or per-model cost
config. Record tokens; let a human read them. Money conversion is a spreadsheet's job.

---

## 5. Risks, conflicts, and regression concerns

| Risk | Which proposal | Severity | Mitigation |
|---|---|---|---|
| A cost ceiling parks a run a human would have paid for | A-01 | Medium | Reuse `hold`: work kept, approval resumes without re-dispatch. Ceiling generous by default, per-org overridable |
| Token counts are unavailable from the CLI backend | A-01 | **High — unverified** | Must be checked first. If `claude -p` does not report usage, A-01 needs a proxy (calls × cap) and the whole design changes. **This is the gating unknown** |
| Two budgets confuse operators | A-01 + A-05 | Medium | One extension command, one vocabulary, both surfaced in the same UI row |
| A retrospective loop teaches the planner to repeat a mistake | A-04 | **High** | Human approval on memory must not be relaxed. Cap recall injection. Keep source-run attribution so a bad lesson is traceable |
| Memory poisoning via a compromised step | A-04 | Medium | Already mitigated by propose→approve; do not add an auto-approve path "for trusted agents" |
| Replay-safe ids collide across attempts | A-06 | Low | Include `attempt`; a retry is a *different* mutation and must not be swallowed |
| Recording spend on every step slows the hot path | A-01 | Low | It is a field on a state dict already being written |
| Budget check adds a new failure mode before dispatch | A-01 | Medium | Must fail *open* on a read error, or a metrics glitch stops the company. Opposite of the grading rule, deliberately — and this asymmetry needs to be written down or someone will "fix" it |

**Conflicts.** None of the six conflict with each other. A-01 and A-05 must ship together or
in that order. A-02 conflicts with nothing but duplicates OBS-08's alerting if built naively.

**Backward compatibility.** All six are additive. A-06 changes the *shape* of `request_id`,
which the run log records — old runs keep their old ids; nothing reads the format.

---

## 6. Validation required before implementation

**A-01 — one experiment gates the entire design.**
Does `claude -p` report token usage in a form the backend can capture? Run it with
`--output-format json` and inspect. **If yes**, the design in §4 stands. **If no**, A-01
becomes "count calls and cap calls", which is weaker, cheaper, and needs its own decision.
Nothing should be built before this is answered — it is a ten-minute check that determines
whether the feature is worth building at all.

Also required: measure real per-step cost across the existing gold runs to set a default
ceiling from evidence rather than a guess.

**A-05.** Prove the current behaviour first: a run at `blocked_cycle_limit`, extended, and
resumed *without re-running completed steps*. The invariant to protect is that extension
never re-dispatches finished work.

**A-06.** A test that a crash between dispatch and complete, followed by a sweep, does not
produce two dispatches. This does not exist today.

**A-04.** Before building: check whether recall actually retrieves the right lessons.
Keyword overlap with 2 entries is untested at any realistic corpus size. Seed 50 synthetic
lessons and measure precision. If plain keywords fail at 50, the mechanism needs rethinking
before it is wired into planning.

**Invariants that must survive all of it:**
1. Red steps remain unapprovable by any code path.
2. The audited actor never writes its own audit line.
3. A control that cannot run never reports a pass.
4. An unresolved outward call is never auto-retried.
5. Nothing reaches an agent's prompt without human approval (memory).

---

## 7. Architecture Opportunity Tracker

Separate from the implementation tracker on purpose. An entry graduates only when its
validation (§6) has passed.

| ID | Mechanism | Problem | Evidence | Value | Depends on | Key risk | Validation gate | Pri | Status |
|---|---|---|---|---|---|---|---|---|---|
| **A-01** | Per-run and per-org cost ceiling, parking via `hold` | Spend is uncapped; `max_cycles` bounds mutations, not money | Real-model run: 3 retries × full model calls, no ceiling **[V]** | Only uncapped path to real loss | A-06 for honest accounting | Token usage may be unavailable | **Does `claude -p` report usage?** | **P0** | Proposed — gated |
| **A-05** | Budget extension, generic over cycles and cost | `blocked_cycle_limit` is terminal, strands work, silent re-drive | Probe P3 **[V]** | Removes a dead end; prevents A-01 duplicating it | — | Two budgets, one vocabulary | Extension must not re-dispatch finished steps | **P1** | Proposed — design with A-01 |
| **A-06** | `request_id` from (run, step, attempt) | WF-04 replay protection unreachable on the autonomous path | Probe P6 **[V]** | Closes a double-spend leak | — | Collision across attempts | Crash-and-resweep produces one dispatch | **P1** | Proposed — ready |
| **A-04** | Run retrospective → memory → planner recall | Run N+1 is exactly as good as run N | `propose_lesson` is the only runtime writer **[V-static]** | Compounding; execution → improvement | Memory recall quality | Teaches the planner to be confidently wrong | Recall precision at 50 entries | **P1** | Proposed — validate recall first |
| **A-03** | Non-model validation for one action class | Three checks, one model family | VAL-06 **[V-static]** | Verifies the quality claim | **TOOL-04** | Scope creep into a rules engine | Needs real data to reconcile against | P1 | Blocked |
| **A-02** | SLA clock and breach event | Runtime has no notion of a deadline | `lead-response` SLA is prose **[V-static]** | Mostly captured by OBS-08 | HOOK-03 | Duplicates OBS-08 alerting | Evidence the alert is insufficient | P2 | Deferred |
| **A-07** | Document the two approval concepts | `decide_step` vs `decide_approval` are distinct and look alike | `service.py:94,157` **[V-static]** | Prevents a whole class of misuse | — | None | — | P2 | Proposed — docs only |
| **A-08** | Retire `scripts/org_state.py` or adopt it | Dead code since inception; runs already carry goals | `state/` = README **[V]** | Removes a decision nobody has made | — | Deleting something later wanted | Decide, do not drift | P2 | Needs a decision |

---

## 8. Dependencies and suggested implementation order

```
  [validate] does claude -p report token usage?
       │
       ├── yes ──► A-06 (replay-safe ids)          cheap, independent, do first
       │             │
       │             └──► A-01 (cost ceiling) ──┬── A-05 (budget extension)
       │                                        └── OBS-08 spend gauge + alert
       │
       └── no  ──► re-scope A-01 as a call cap; A-05 still stands on its own

  A-04 (retrospective) ── validate recall precision first ── independent of the above
  A-03 (non-model validation) ── blocked on TOOL-04 (a human authorizing a provider)
  A-07, A-08 ── documentation and a decision; no code
```

**Leverage.** A-05 built generically eliminates the resume path A-01 would otherwise need.
A-06 built first makes A-01's accounting trustworthy. Those three are one project.

**Independent:** A-04, A-07, A-08.
**Must not coexist naively:** A-02 and OBS-08's approval-age alert — one or the other.

---

## 9. Rejected, deferred, or needing more evidence

**Rejected outright:**

- **A workflow-engine replacement (Temporal, Prefect).** The state machine is 553 lines, is
  hash-chained, fences its steps, and has been verified end to end. Adopting an engine means
  rebuilding the audit chain and the gate model on somebody else's primitives. Borrow *ideas*
  (resumable budgets — A-05), not the dependency. Also: `pyproject.toml` declares zero
  dependencies and a test enforces it. That is an asset.
- **A message bus between agents.** `send_message` on the maker-checker edge already carries
  typed, hash-verified envelopes. A bus would add a broker to a single-host system that
  processes work in seconds. **YAGNI.**
- **A supervisor agent watching the other agents.** This is the most tempting and the worst
  idea here. It adds a model in the control path, correlated with the models it supervises
  (VAL-06's flaw, promoted to the control plane), and it cannot be audited by anything. The
  supervisor this product needs is `scheduler.py` plus alerts — deterministic and already built.
- **A policy DSL.** `policy.json` maps 17 actions to three colours. A DSL would make the one
  control the whole product rests on harder to read. The current file can be checked by eye;
  that is a feature.

**Deferred, evidence needed:**

- **Per-agent sandboxing beyond the workspace.** The measured containment (`Read(./**)` +
  `dontAsk`) holds. Containers are the next rung, and the right time is the Docker migration
  the target stack already plans — not before.
- **Multi-host coordination.** The single-instance lock is file-based, so a second machine
  with its own disk would not see it **[V-static]**. Real, but no second host exists.
  Revisit at the Docker/VPS migration, not now.
- **A-02 SLA clock.** As above.

---

## 10. Best candidate to validate first

**A-01's gating question, then A-06.**

**Why this is the strongest next step.** Every other control in this product fails safe. A
grader that cannot run parks the step. An audit log that cannot be written stops the gate. An
outward call with an unknown outcome is never retried. The cost path is the single exception:
it fails *expensive*, silently, and now that the company runs unattended on a schedule and on
webhooks, it fails expensive **while nobody is at the keyboard**. That asymmetry is the most
important thing this review found.

**What must be proven before implementation.** One question: does `claude -p` report token
usage in a machine-readable form? Everything about the design turns on the answer, and it is a
ten-minute experiment. Do not design further before running it. Then measure actual per-step
cost across the existing gold runs, so the default ceiling comes from evidence rather than a
number somebody liked.

**What existing behaviour must be protected.** The budget check runs *before* dispatch, which
puts a new failure mode in front of every step. It must fail **open** — a read error on the
spend counter must not stop the company. That is the opposite of the grading rule, and the
asymmetry is deliberate: a grader that cannot run risks shipping bad work, while a spend
counter that cannot run risks only overspending, which an alert catches. Write that reasoning
down next to the code, or someone will later "fix" it into failing closed and hand the runtime
a new way to halt itself.

Also protected: `hold` must keep the deliverable and resume without re-dispatch. That is
VAL-07's contract, and a budget hold reusing it inherits the obligation.

**What would make it safe to move forward.** Usage numbers captured from a real dispatch; a
measured cost distribution across the four gold runs; a test that a run crossing its ceiling
parks rather than fails; and a test that approving that hold completes the step *without a
second model call*. With those four, A-01 is a small change to a system that already knows how
to park work and ask a person.

**If the gating question answers "no",** A-01 becomes a call-count cap — cruder, still
worthwhile, and a different enough design that it deserves its own decision rather than being
bent to fit this one.
