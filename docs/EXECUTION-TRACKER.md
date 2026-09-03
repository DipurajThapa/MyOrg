# Execution Tracker — the one source of truth

**Created 2026-09-03.** This file is where work is tracked. The architecture reviews
([cycle 2](ARCHITECTURE-OPPORTUNITIES-2026-09-01.md), [cycle 3](ARCHITECTURE-OPPORTUNITIES-2026-09-02.md))
and the [REV2 audit](AUTONOMY-AUDIT-2026-09-01-REV2.md) keep their reasoning; **their status
columns are superseded by §4 here.** IDs are stable: `A-*` (cycle 2), `B-*` (cycle 3),
`NOTIFY-01` etc. (REV2). New items from this plan continue the `B-` series.

Status vocabulary: `Proposed` · `Validating` · `Ready` · `In Progress` · `Blocked` · `Done` ·
`Deferred` · `Rejected`. Nothing is `Done` without a test or a measurement named in the row.

Evidence tags: **[V]** executed here · **[V-static]** read in the code · **[I]** inference.

---

## 1. Current-state assessment

### Healthy — leave alone

- **The state machine and its audit.** `mutate()` writes the audit entry before the run event,
  refuses terminal runs, replays identical `request_id`s and refuses reused ones. Every gate
  (green/yellow/red), every claim fence, every terminal state goes through it. This is the
  spine and it is sound [V-static].
- **The governance boundary.** Red is unapprovable by any code path; yellow parks; webhook
  payloads select a pre-registered trigger by one field and never become prompt text [V-static].
- **The quality loop.** Structural gate → acceptance grader → maker-checker, with the grader
  failing *closed* (hold for a human) and the cost ceiling failing *open* — both documented at
  the call site with the reasoning [V-static].
- **The supervisory loop.** `sweep` isolates one run's failure from the others; `single_instance`
  prevents a second sweeper; `Shutdown` finishes its pass. Intake, projection and escalation are
  each wrapped so they can never stop the driver [V-static].
- **The dependency posture.** Zero runtime dependencies, enforced by test. Nothing below needs
  one.

### Off track — and what matters most, in order

1. **Nobody is told anything.** Every detection path — dead-end runs, waiting approvals,
   stalled runs, proposed lessons, and all six Prometheus autonomy rules — terminates in a file
   (`_outbox.jsonl`) or a scrape target nobody has confirmed is scraped. `MYORG_NOTIFY_COMMAND`
   is not in `deploy/myorg.env.example`, not in any systemd unit, not in the runbook; it
   appears only in audit documents [V]. **This is the single highest-leverage item on the board
   and it is a configuration value, not code.** Every "alert" proposed anywhere is moot until
   it is set.
2. **A human cannot stop a run.** No cancel verb exists. `reject` requires the step to be
   parked; a green-only run never parks [V-static]. (B-02)
3. **The external-worker fence is not real.** `agent_api.submit` reads the *current*
   `claim_token` from state and passes it to `check_claim`, so it always passes — the token is
   never round-tripped through the worker. And `heartbeat` renews the lease, not the claim, so
   the driver adopts a live worker's step after 600 s [V-static]. Two mechanisms, one
   ownership question, neither answered. (B-01)
4. **Roughly half the model calls are invisible to the cost ceiling.** `charge()` rides on
   `complete`/`fail`/`hold` only. Planner (1–4 calls per trigger), checker (1 per review, per
   RETURN cycle), and brief (1 per parked yellow step) contain **no** cost handling
   [V-static: zero `cost`/`spend` references in `planner.py`, `checking.py`, `briefing.py`].
   Cycle 3 under-stated this as "planner only". (B-04)
5. **A dead control that should be the pause switch.** `set_organization_status(suspended)`
   exists in `db.py` and `admin.py` and is enforced nowhere — `auth.py` checks *actor* status,
   never org status; `sweep` and the webhook route never look [V-static]. Cycle 3 proposed a new
   flag file for pause; that would be a third mechanism for a thing that already has a dead one.
   (B-03, redesigned)

### Misleading

- REV2 marks "Leases / liveness" ✅. It is wired in (`sweep` calls `reclaim`) but has never run
  outside tests, and the mechanism it duplicates is the one that actually arbitrates. ✅ overstates
  it.
- Two approval surfaces exist — `approval_server.py` (port 8787, no auth, briefs + memory
  approval) and the Control Center via `api.py` (`/v1/decisions`, roles, reasons). REV2 lists
  both as ✅. That is two front doors for the most sensitive action in the product. (B-09)

---

## 2. Architecture and process corrections

| Decision | What | Why |
|---|---|---|
| **Keep** | `mutate`, gate model, audit-before-event, fail-open/closed asymmetry, `single_instance`, zero-dependency rule | Sound, verified, and the reason the fixes below are small |
| **Keep** | Cycle 2's four rejections (bus, supervisor agent, policy DSL, workflow engine) and cycle 3's three (parallel sweep, retry-backoff layer, failed-run notice) | Re-read against current code; reasoning holds. No new evidence |
| **Consolidate → one liveness record** (B-01) | Delete `leases.py` and `leases.json`. The claim (`holder`, `claim_token`, `claim_expires_at`) is the only liveness record. `/v1/claim` returns the token; `/v1/submit`, `/v1/fail` require it back; `/v1/heartbeat` becomes a `renew-claim` mutation. `sweep.reclaim` goes away — `drive_step` already adopts expired claims | Fewer concepts, net deletion, and it makes the fence real. Cycle 3 got the direction right but missed that the token is never round-tripped, which is the larger defect |
| **Consolidate → use the dead flag** (B-03) | Do not add a pause file. Make `organizations.status = suspended` mean: `intake()` skips the org, `/v1/webhooks` refuses for it, `TokenService.issue` refuses for it. In-flight runs keep driving; `mirror`/`watch` keep running; a gauge `myorg_org_suspended` and an alert say it is paused | Extends a mechanism that exists rather than adding one. "Stop new work" + B-02 "stop this run" covers the operator's need without a third pause concept |
| **Change → generalise, don't enumerate** (B-02) | `health.classify` hardcodes `("rejected", "rejected_by_checker")` and `blocked_*`. Replace with "any status in `core.TERMINAL_RUN` that is not `completed`/`blocked_human` is FAILED". Same for `escalation.DEAD_END` and `projection.COARSE_STATUS` | Otherwise `cancelled` falls through `classify` to STALLED and raises a false `RUN_STALLED` notice forever. Generalising closes the class, not the instance |
| **Broaden** (B-04) | From "charge planning cost" to "every model call is charged somewhere". Planner → seeds `spend_usd` at `create_run`. Checker → `check_*` mutations accept `spend`, like `complete`/`fail`/`hold` already do. Brief → measure first; if it is pennies, document the undercount rather than invent a transition for it | Cost outside the ceiling is one problem, not three |
| **Trim** (B-05a) | Ship the alert on `myorg_spend_usd_total`, but only after NOTIFY-01 — an alert with no delivery is a comment | Ordering, not scope |
| **Trim** (B-06) | Defer the sweep pass deadline, unchanged from cycle 3 | No load makes it observable yet |
| **Defer with a gate** (B-08, new) | The agent API and the in-process driver are two dispatch paths competing for the same `ready` steps with no ownership rule — `open_work` offers every ready step; `advance` dispatches every ready step [V-static]. B-01 makes the *fence* correct; it does not say who *should* do the step | No external worker exists. Decide the rule (e.g., owners registered as external are skipped by the driver) when the first one is admitted, not before |
| **Decide** (B-09, new) | One approval surface. Recommend: the Control Center via `api.py` is authoritative (it has identity, roles, reasons, org scoping). `approval_server.py` becomes the offline fallback or is retired. Cancel (B-02) is built into `service.py`/`api.py` **only** | Two front doors for approvals is two places to get authorization wrong. Same shape as A-08: decide, do not drift |
| **Reconsider — A-02 stays deferred** | Cycle 2 deferred the SLA clock as duplicating OBS-08's approval-age alert | Still true, and now also gated on NOTIFY-01. Nothing new |
| **Reconsider — A-04 stays proposed** | Run retrospective → memory | Recall precision gate unchanged; not on the critical path |

---

## 3. Recommended execution path

```
Week 0 — configuration and evidence, no code
  NOTIFY-01  set MYORG_NOTIFY_COMMAND; add to env example + unit + runbook   [human picks channel]
  B-05a      add the total-spend alert rule                                   [after NOTIFY-01]
  B-01       write the failing test (heartbeat past CLAIM_SECONDS → driver adopts)
  B-02       run the race experiment (cancel while a dispatch is in flight)
  B-04       measure one plan, one check, one brief — real dollars

Week 1 — the human stop, one project
  B-02  cancel-run: core verb + service/api route + classify/escalation/projection generalised
  B-03  suspended means suspended: intake, webhook, token issue, gauge, alert
  B-09  decision recorded: which approval surface is authoritative

Week 1–2 — independent, parallel with the above
  B-01  one liveness record (delete leases; token round-trip; renew-claim)
  B-04  charge planner + checker; document brief

Then — deferred behind evidence
  B-05b  fleet ceiling            ← a week of B-05a data
  B-06   pass deadline            ← observable head-of-line blocking
  B-08   dispatch ownership rule  ← first external worker
  A-04, A-03, A-02, A-07, A-08    ← unchanged from cycle 2
```

**Dependencies that matter.** NOTIFY-01 before any alert. B-02's race experiment before B-02's
code. B-01's failing test before B-01's deletion. B-09's decision before B-02's UI button (so
it is built once).

**Parallel.** B-01 and B-04 touch different files from B-02/B-03 and can proceed alongside.

---

## 4. Authoritative task tracker

Compact table, then a detail block for every row that is active or next.

| ID | Outcome | Status | Pri | Depends on | Next action |
|---|---|---|---|---|---|
| **NOTIFY-01** | A person is actually told when the company needs one | **Blocked** (human: choose channel) | **P0** | — | Operator sets `MYORG_NOTIFY_COMMAND`; engineer adds it to env example, units, runbook, and a startup warning when unset |
| **B-02** | A named human can stop any non-terminal run in one action | **In Progress** — verb, API route, generalised terminal handling, runbook: **done 2026-09-03**, 12 tests + 277 existing green. Control Center button: **blocked on B-09** | **P0** | B-09 (button only) | Decide B-09, then add the button (needs a run list the UI does not have yet) |
| **B-01** | One liveness record; the external-worker fence is real | **Ready** — both failing tests fail as predicted 2026-09-03 (§5.3) | **P1** (P0 before any external worker) | — | Implement per §5.3 |
| **B-04** | Every model call is charged to a run or documented as uncharged | **Validating** | P1 | — | Measure plan / check / brief cost (§5.4) |
| **B-03** | `suspended` stops new work without silencing the watchers | **Ready** | P1 | B-02 (ships with) | Implement per §5.5 after B-02's experiment |
| **B-05a** | Total spend is alerted on | **Ready** | P1 | NOTIFY-01 | Add the rule; verify it fires against a synthetic gauge |
| **B-09** | One authoritative approval surface | **Proposed** (decision) | P1 | — | Owner decides: Control Center authoritative; `approval_server.py` fallback or retired |
| **B-08** | An ownership rule between the driver and external workers | **Deferred** | P2 | B-01; first external worker | None until an external worker is planned |
| **B-06** | A sweep pass cannot be held indefinitely by one run | **Deferred** | P2 | B-03 (same function) | None until head-of-line blocking is observed |
| **B-05b** | An enforcing fleet spend ceiling | **Deferred** | P3 | B-05a + a week of data | None |
| **B-07** | Surface `SweepResult.failed` | **Rejected** | — | — | — (STALLED at 30 min covers it) |
| A-01 | Per-run cost ceiling | **Done** | — | — | Re-derive the `$5` default after B-04 measures planning cost |
| A-05 | `extend-budget` | **Done** | — | — | — |
| A-06 | Replay-safe request ids | **Done** | — | — | — |
| A-09 | Trimmed dispatch profile | **Done** | — | — | — |
| A-10 | Cache warmth | **Rejected** (measured, build nothing) | — | — | — |
| A-04 | Run retrospective → memory → planner | **Proposed** | P2 | Recall precision at 50 entries | Not on the critical path; revisit after the stop controls |
| A-03 | Non-model validation for one action class | **Blocked** (TOOL-04, human authorises a provider) | P1 | TOOL-04 | — |
| A-02 | SLA clock | **Deferred** | P2 | NOTIFY-01, evidence OBS-08 is insufficient | — |
| A-07 | Document `decide_step` vs `decide_approval` | **Proposed** (docs only) | P2 | — | Fold into B-09's decision record |
| A-08 | Retire or adopt `scripts/org_state.py` | **Proposed** (decision) | P2 | — | Owner decides; recommend retire |

### Detail — active and next rows

**NOTIFY-01 — delivery**
- *Changing:* nothing in the runtime. `deploy/myorg.env.example` gains `MYORG_NOTIFY_COMMAND`;
  `myorg-scheduler.service` passes it; `OPERATIONS-RUNBOOK.md` documents it; `scheduler.serve`
  logs one warning per start when it is unset and `--supervised` is on.
- *Validation:* one notice raised by a real sweep reaches the chosen channel.
- *Done when:* the warning exists and a delivered notice is on record.
- *Risk:* the delivery command itself is an outward action executed by the daemon. Its
  configuration is a human act (yellow); the command must not be settable from any API.
- *Blocker:* a human must choose the channel. Nothing else blocks it.

**B-02 — cancel-run** — spec in §5.2.

**B-01 — one liveness record** — spec in §5.3.

**B-04 — every model call charged** — spec in §5.4.

**B-03 — suspended means suspended** — spec in §5.5.

**B-05a — total spend alert**
- *Changing:* one rule in `deploy/prometheus-alerts.yml` on `myorg_spend_usd_total`, threshold
  from B-04's measurements (placeholder `> 25` per scrape window until then).
- *Validation:* rule syntax checked; fires against a synthetic value.
- *Done when:* the rule is in the file, NOTIFY-01 is set, and the runbook names the response.
- *Risk:* none. *Blocker:* NOTIFY-01 — until then it is a comment in a YAML file.

**B-09 — one approval surface (decision)**
- *Question:* is `approval_server.py` retired, kept as an offline fallback, or kept as-is?
- *Recommendation:* Control Center via `api.py` is authoritative — it has identity, roles,
  reasons and org scoping; the local server has none. Keep the local server only if an
  offline path is a stated requirement; if kept, it must never gain a verb the API lacks.
- *Done when:* the decision is written into `company/operating-principles.md` §7 and A-07's
  documentation is folded in.

---

## 5. Immediate implementation / validation specifications

### 5.1 Common rules for everything below

- Every state change goes through `company_runtime.mutate` with a `request_id`; replays are
  no-ops that say so. No new bypasses of `mutate` — `extend_budget` needed one and is the only
  one allowed.
- Every human verb requires a named approver and writes a yellow audit entry, like `approve`
  and `extend_budget`. No human verb appears in `runtime/tools.json` or is reachable from a
  dispatched agent's grant.
- Every new terminal state is added to `core.TERMINAL_RUN` **and** handled by the generalised
  `classify` / `DEAD_END` / `COARSE_STATUS` — a test asserts the three agree with
  `TERMINAL_RUN`.
- Tests are `tests/test_*.py`, stdlib `unittest`/`pytest`-compatible, no new dependencies.

### 5.2 B-02 — `cancel-run`

**Validate first (the race experiment, ~10 min).** With the stub backend, make `dispatch` block
(a backend that sleeps), start `advance` in one process, and in another run the cancel verb
from §5.2's design below (or, before it exists, any mutation that sets a terminal status).
Observe: the blocked dispatch returns → `finish` → `mutate` refuses "run is terminal" →
`SystemExit` → `ExecutorError` → caught by `sweep`, other runs still driven. **Record the
outcome in this file.** If the error escapes `sweep`, or `advance` loops, stop and redesign
before writing the verb.

> **Run 2026-09-03 [V].** Stub backend; a `mutate` setting `run_status="cancelled"` fired from
> inside the first dispatch. Observed:
> - `advance` raised `ExecutorError("could not complete frame-goal: run is terminal: cancelled")`;
>   `sweep` caught it and drove the other run to 3/3 green steps. **As predicted.**
> - The evidence file `xp-b02.frame-goal.evidence` was written before the refused `complete`
>   and survived. **As predicted.** The step stayed `in_progress` with the executor's holder —
>   the verb must release claims (already in the design).
> - `health.classify` reported the cancelled run as **`running`**, and the next sweep drove it
>   again (a harmless no-op today, a false `RUN_STALLED` notice after 30 min). **Confirms the
>   generalisation requirement** — it is not optional.
> Design stands. Script: session scratchpad `experiments.py`; will become tests 1–3 and 5.

> **Built 2026-09-03 [V].** `cancel_run` in `company_runtime` (+ `cancel-run` CLI);
> `"cancelled"` in `TERMINAL_RUN`; `service.cancel_run` + `POST /v1/runs/{id}/cancel`
> (decision-owner, human, org-scoped like `decide_step`); `drive_step` skips grading when the
> run ended mid-dispatch; `health.classify` now derives from `TERMINAL_RUN`; `DEAD_END` and
> `COARSE_STATUS` map `cancelled`. `tests/test_cancel.py` (12) covers §5.2 tests 1–5 plus the
> service guards; every affected suite (277) passes. Runbook: "Stopping a run".
> **Not done:** the Control Center button — waits on B-09 and on a run list the UI lacks.
> Two audit lines per cancel (the human's yellow entry + `record_terminal`'s green one) —
> same shape as `reject`, kept for consistency.

**Design.**
- *Subsystem:* `company_runtime` (verb), `service.py` + `api.py` (route), `health`,
  `escalation`, `projection` (generalisation), Control Center (button).
- *Verb:* `cancel-run <run_id> --approver --reason --request-id`. `change()`: refuse if not
  `active` (the message says what it is instead — REC-11's lesson); set
  `run_status="cancelled"`, `cancelled_by`, `cancel_reason`; release every live claim so no
  holder is left pointing at it. `audit()`: `category="yellow"`, `approval="granted"`,
  `outcome="blocked"`, note = reason. Add `"cancelled"` to `TERMINAL_RUN`; `record_terminal`
  fires as it does for every terminal state.
- *Route:* `POST /v1/runs/{run_id}/cancel` with `{reason}`; `service.cancel_run` requires
  `decision-owner` **and** `actor_type == "human"`, org-scopes the run exactly as
  `decide_step` does (unknown run and other-org run give the same answer).
- *Cheap early exit:* in `drive_step`, re-read `run_status` once before `acceptance_failure`
  (the grading call). If terminal, skip grading and return. This saves the most expensive
  post-cancel call; it is not a correctness requirement.
- *Generalise:* `health.classify`, `escalation.DEAD_END`, `projection.COARSE_STATUS` derive
  from `TERMINAL_RUN` rather than enumerating. `DEAD_END["cancelled"]` reads "was stopped by
  {cancelled_by}".

**Invariants.** Hash chain unbroken (cancel is an appended event). No evidence file deleted.
Red-step invariant untouched — cancel ends a run; it never performs the blocked action. Not
reachable by any agent grant.

**Failure and race behaviour.** Cancel takes the run lock like every mutation; an in-flight
dispatch's `complete`/`fail`/`hold` is refused afterwards. **Known, accepted undercount:** the
in-flight dispatch's cost is charged on the transition that is refused, so a cancelled run's
`spend_usd` excludes at most one dispatch + one grade. Documented in the verb's docstring.

**Observability.** `myorg_runs_cancelled_total` is unnecessary — the audit log and the
projection's coarse status already carry it. The Control Center's run list shows `cancelled`
with the approver.

**Tests (all four must exist before Done).**
1. Cancel with a step `in_progress` and a live claim → `cancelled`, claim released, run never
   moves again on a subsequent sweep.
2. A `complete` arriving after cancel is refused; `advance` raises `ExecutorError`; `sweep`
   records it in `failed` and drives the next run.
3. Every evidence file present before cancel exists after and hashes to the log's record.
4. Cancel without an approver is refused; cancel via a non-human principal is `Forbidden`;
   cancel on a terminal run is refused with a message naming the current status.
5. `TERMINAL_RUN` ⊆ handled-by-`classify` ∪ handled-by-`DEAD_END` ∪ handled-by-`COARSE_STATUS`.

**Done when:** the race outcome is recorded here; five tests pass; one real run is stopped from
the Control Center and the audit log shows who, when, why, and what it had produced.

### 5.3 B-01 — one liveness record

**Validate first (the failing test).** Claim a step via `/v1/claim`; advance the clock past
`CLAIM_SECONDS` while calling `/v1/heartbeat`; run `sweep`; assert the driver did **not** take
the step. Expected today: **fails** (driver adopts). Also: submit with a deliberately wrong
token and assert refusal — expected today: **passes wrongly**, because `submit` never uses the
worker's token. If either expectation is wrong, this finding is withdrawn and the row is
`Rejected`.

> **Run 2026-09-03 [V].** `MYORG_CLAIM_SECONDS=1`, `MYORG_LEASE_SECONDS=600`, stub backend,
> `agent_api` called in-process.
> - **(a)** Worker claimed `frame-goal`; after 2 s the lease was live and the claim was not;
>   `heartbeat` succeeded; one sweep then **took and completed the step in-process**
>   (`step.taken` by the driver, worker's lease still live). **Fails as predicted.**
> - **(b)** Worker claimed (`token #2`); after expiry another holder took it (`token #3`); the
>   worker's `submit` was **accepted** — it read `#3` from state and passed `check_claim`.
>   **Fence bypassed as predicted.**
> Finding stands. Both cases become the inverted tests below.

**Design.**
- *Delete:* `runtime/leases.py`, `runtime/runs/leases.json`, `sweep`'s `reclaim` call,
  `leases.*` uses in `agent_api`.
- *Add one verb:* `renew-claim <run> <step> --holder --claim-token --request-id` in
  `company_runtime` — refuses unless the supplied token is current and the holder matches;
  extends `claim_expires_at` by `CLAIM_SECONDS`. It is a mutation (costs a cycle); heartbeat
  cadence should therefore be ~half of `CLAIM_SECONDS`, not every few seconds — document this
  in the API response (`renew_before`).
- *API contract:* `/v1/claim` returns `claim_token` and `claim_expires_at` (keep
  `lease_expires_at` as an alias for one release). `/v1/submit`, `/v1/fail`, `/v1/heartbeat`
  **require** `claim_token` in the body and pass it through to `check_claim`. A missing token
  is a 400, not a bypass — the "None means human at the CLI" rule in `check_claim` must not
  apply to the API.
- *Expiry semantics:* an unrenewed claim expires and the driver adopts the step (existing
  `take` path). No attempt is consumed. `reclaim`'s "fail the step" behaviour is dropped —
  adoption is the better outcome and is already how the driver treats its own expired claims.

**Invariants.** One holder per step at any instant, arbitrated only by `claim_token`. A stale
holder's write is always refused.

**Race behaviour.** Renewal and adoption both go through `mutate` under the run lock; whichever
lands first wins and the other is refused with a legible message.

**Tests.** The two above, inverted to pass; `renew-claim` replay is a no-op; wrong token
refused on all three API writes; missing token is 400; adoption after expiry works with no
lease file present.

**Done when:** `leases.py` is gone, `test_dependencies`/suite green, `module-agent-api.sh`
passes, and REV2's "Leases / liveness" row is annotated "superseded by claims (B-01)".

### 5.4 B-04 — every model call charged

**Validate first (measure).** Using the existing measurement scripts' approach
(`scripts/measure_dispatch_cost.py`), capture `cost_usd` for: one `plan()` with one repair; one
`drive_check`; one `write_brief`. Record the three figures here. These decide the design of
the brief case and re-derive A-01's default.

**Design.**
- *Planner:* `plan()` returns `(workflow, cost_usd)` by summing `Output.cost_usd` across its
  calls; `start_queued` passes it to `create_run(spend=...)`, which seeds `state["spend_usd"]`
  and records it as `planning_spend_usd` so the number is explainable.
- *Checker:* `check_approve/return/reject` accept `--spend`, call `charge()` exactly as
  `complete`/`fail`/`hold` do; `checking.send_verdict` passes the review call's cost.
- *Brief:* if the measured cost is below ~2% of a graded step, document the undercount in
  `briefing.write_brief`'s docstring and stop. If not, charge it on the next human decision
  (`approve`/`reject`) as `brief_spend_usd` — the only transition the parked step will make.
- *Ceiling:* after seeding, `over_budget` sees planning cost on the first step. Re-derive
  `MYORG_RUN_CEILING_USD`'s default from a measured run that includes planning; update the
  docstring's numbers.

**Invariants.** `spend_usd` never decreases; undercounting is the only permitted error
direction (already the rule in `charge()`'s docstring).

**Tests.** Planner cost appears on the run at creation; checker cost accumulates per review;
a run whose planning alone exceeds the ceiling parks its first step with the over-budget note.

**Done when:** three measurements recorded here; tests pass; the ceiling default and its
docstring reflect a run that includes planning.

### 5.5 B-03 — `suspended` means suspended

**Design.**
- *Enforce in three places:* `scheduler.intake` skips a suspended org (logs once per pass);
  `api._webhook` refuses with the same single refusal shape it uses for every rejection (do not
  leak that the org exists); `TokenService.issue` refuses for a suspended org (actor status is
  already checked at `auth.py:84`; add the org check beside it).
- *Do not touch:* `sweep`'s drive loop, `mirror`, `watch`. In-flight runs finish or are
  cancelled individually via B-02. A suspended company still projects and escalates.
- *Observability:* `RuntimeGauges` gains `myorg_org_suspended{org}`; an alert fires after 6 h
  suspended so a forgotten pause is not mistaken for a quiet company.
- *Surface:* the admin CLI already has `org-status`. The Control Center gets a switch only if
  B-09 decides the Control Center is authoritative; the switch calls a new
  `PUT /v1/organization/status` requiring `system-admin`.

**Tests.** Suspended org: intake starts nothing, webhook refused identically to a bad
signature, token issue refused, an existing active run still advances, gauge reads 1.

**Done when:** tests pass and the runbook has a "pause the company" entry that names
`org-status --status suspended` and B-02's cancel as the two halves.

---

## 6. Critical challenges and missing considerations

1. **The reviews measured everything except whether anyone would hear about it.** Both cycles
   proposed alerts and notices; neither noticed the delivery command is documented nowhere an
   operator would look. Fixing that is worth more than any item in either tracker.
2. **Cycle 3 under-called B-01.** It framed the defect as lease-vs-claim expiry. The larger
   defect is that the API never round-trips the token, so the fence is decorative for external
   workers. The consolidation fixes both, but the failing test must cover both.
3. **Cycle 3 under-called B-04.** "Planner only" was wrong; the checker is uncharged too, and
   RETURN cycles multiply it. Measured numbers decide the brief case — do not design it first.
4. **Cycle 3's pause design would have been the third mechanism.** A dead `suspended` flag
   already exists. Extending it is smaller and leaves one concept.
5. **New terminal states are a class of bug, not an instance.** `classify` enumerates terminal
   statuses by hand; `cancelled` would have silently become "stalled". Generalise from
   `TERMINAL_RUN` and add the agreement test, or the next terminal state repeats it.
6. **Two dispatch paths, no ownership rule (B-08).** Not urgent — no external worker exists —
   but it is the design question behind B-01, and B-01 deliberately does not answer it. Do not
   let an external worker be admitted without the rule.
7. **Two approval surfaces (B-09).** The most sensitive action in the product has two front
   doors with different authorization. That is a decision, not a build, and it should be made
   before B-02 adds a third verb to either.
8. **A cancelled run under-reports its cost by one dispatch.** Accepted and documented rather
   than solved with a new event type. If B-05a's data shows cancellations are frequent, revisit.
9. **`over_budget` re-reads the whole log per ready step per pass.** Correct today; will be the
   first latency symptom under load. Watch, do not fix.
10. **Nothing here needs a dependency, a service, an agent, or a framework.** Every change is a
    verb on `mutate`, a deletion, a config value, or a generalisation of an enumeration. If an
    implementation starts needing more than that, the design is wrong — stop and come back here.

---

## 7. Status-update protocol

When progress is reported: update the row's status, evidence and next action in §4; record
measurements and experiment outcomes inline in §5; move nothing to `Done` without the named
test or measurement; reopen `Deferred`/`Rejected` rows only with new evidence, and say what
it was. Do not create a second tracker.
