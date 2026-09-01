# Autonomy Audit & Project Tracker — REV2, 2026-09-01 (post-P0 session)

Supersedes `docs/history/AUTONOMY-BASELINE-2026-09-01.md` (REV1, same day, written before the
executor/planner/scheduler/memory work landed). Every claim is tagged:

- **[V]** Verified by reading code and/or executing it on this host
- **[D]** Documented or intended, not verified as working
- **[I]** Inference from verified facts
- **[?]** Unknown — insufficient evidence

Commands run for this revision are listed in §10.

---

## 1. Executive Assessment

**What it is in practice [V].** MyOrg is now a *working, governed, single-host workflow
engine for LLM agents*, plus a large body of role/policy documentation. Unlike REV1, this
is no longer a control plane with no data plane: a run can be planned from a plain-words
goal, driven step-by-step by real `claude -p` calls to the owning department, independently
reviewed by a checker agent, graded against acceptance criteria, halted at yellow/red
gates with a decision brief, mirrored into an operator read model, and replayed from a
hash-chained event log. Four live multi-agent runs exist on disk (`runtime/runs/gold-*`).

**What it is intended to become [V, from `CLAUDE.md` §1–§3, `company/operating-model.md`,
and the settled decision recorded in REV1 §9].** An organization of 17 department agents
that acts on plain-language business goals with *exception-based* human involvement:
autonomous inside 🟢 green, approval-gated at 🟡 yellow, handed back at 🔴 red.

**How close it is.** The **execution spine is real and tested**. What is still missing is
everything that connects that spine to the world:

1. **Agents cannot act — only write. [V]** `runtime/backends.py:56` dispatches every step
   as `claude -p … --allowedTools ""`. Tools are disabled by design so the driver is the
   only writer. The consequence is that every department's output is prose about work,
   never the work: no file read, no query, no system touched. A "CFO reconciliation" is a
   description of a reconciliation. This is the single largest gap between the current
   product and an autonomous organization.
2. **Nothing can start work except a person. [V]** No `/webhooks` route (`runtime/api.py`
   route table has none, though `WebhookVerifier` exists in `connectors.py`), no cron or
   calendar trigger, no SLA clock, no installed scheduler service (`deploy/` ships api,
   backup and maintenance units only). `scheduler.serve()` exists but is bounded by
   `max_passes` and must be launched by hand.
3. **Governance evidence is still self-reported. [V]** `logs/audit-log.jsonl` is unchanged
   at 7 hand-authored lines dated 2026-07-14, and no `.py`/`.ts`/`.sh` outside `tests/`
   appends to it. REV1's RCA-5 corrective (`runtime/audit.py`) was never built, and the
   tracker ID `OBS-05` was re-used for stall detection — so the gap silently left the
   tracker while the RCA remained.
4. **The read-model half has never been instantiated here. [V]** `runtime/data/` does not
   exist, so no SQLite store, no orgs, no projection rows. `state/` still contains only
   `README.md`: `scripts/org_state.py` (goals/tasks/decisions) has never recorded anything.
5. **The suite is red, and the tracker says it is green. [V]** `bash tests/run.sh` →
   `SUITE: FAIL`, 5 failing checks (2 × `CLAUDE.md` size, `demand-gen` per-send gating,
   DSR send gating, breach-notification gating). REV1 `TEST-01` claims "only CORE's two
   size checks fail". Documentation has drifted ahead of the code again, in the same
   direction as before.

**Strongest parts [V].** The state machine (`company_runtime.py`): hash-chained
append-only events, idempotent mutations, retry/cycle caps, evidence integrity re-verified
at check time, stale-revision protection, six terminal states, red steps unapprovable by
any code path. The maker-checker and grading loops. The cross-platform lock
(`filelock.py`). The security posture of `api.py`/`auth.py`/`db.py`.

**Can it be completed incrementally?** **Yes. [I]** There is no deep architectural defect
left. The JSONL-vs-SQLite split — REV1's worst finding — was resolved deliberately, not
merged: the log is the system of record for execution, SQLite is identity + operator read
model, and `projection.py` mirrors one way only. The remaining work is additive: give
agents governed tools, give the system triggers, make the audit trail a side effect
instead of a behaviour, and run the loop as a service.

---

## 2. Intended Autonomous-Organization Architecture (reconstructed from evidence)

**Purpose [V].** A user states a business goal in plain words; the correct department acts
without the user naming a skill or agent (`CLAUDE.md` north star).

**"Autonomous" here means [V, `CLAUDE.md` §3 + `runtime/policy.json`, 17 actions]:**
unbounded autonomy inside 🟢 green; draft-then-ask at 🟡 yellow (`awaiting_approval`,
requires `--approver` + `--approval-ref`); never at 🔴 red (`blocked_human`, no code path
can approve it). Human involvement is meant to be *exceptional*, not continuous.

**Roles [V].** 17 department agents in `.claude/agents/*.md`, each with scope, inputs →
outputs, success criteria, and explicit decide/consult/escalate rights. The Chief of Staff
is the session root and the planner persona. Ownership is enforced at runtime:
`agent_exists()` gates every workflow owner and checker.

**Capabilities [V].** 13 local skills under `.claude/skills/`; 137 distinct skill
references across the agent files, of which 124 are *declared* external dependencies in
`company/skills.manifest.json` with `verified_here: false`, 13 resolve locally, 0
unresolved. Declaration closed the accounting gap; it did not make any of the 124 usable.

**Coordination [V].** DAG dependencies release dependents on completion
(`release_dependents()`); each step's prompt carries its direct upstream evidence,
re-hashed before it is trusted and clipped at 6,000 chars (`upstream_handoffs()`); the
checker's RETURN/REJECT reasons feed the maker's next attempt (`last_feedback()`).

**Memory [V].** `runtime/memory.py` — append-only, hash-chained, org-scoped. Agents
*propose*; only human-approved entries are recalled into later prompts. In use, lightly:
`memory/default.memory.jsonl` has 2 entries.

**Intended full cycle [I, assembled from the above].** Trigger → plan → validate → drive
ready steps → agent produces evidence → structural + acceptance grading → checker review →
green advances, yellow parks with a brief, red hands back → dependents release → outcome
recorded → lesson proposed → run completes or escalates.

**Where the design is unclear or conflicting [V].**
- Repo docs (`README.md`, `docs/RUNTIME-AUDIT.md`) still describe permanent human-in-the-loop
  as the *goal*; the settled target is exception-based autonomy. They describe today, not
  the destination, and nothing states that distinction inside the repo.
- `--allowedTools ""` is described as a safety property, but under the autonomy target it
  is also the thing that prevents the org from doing work. No document reconciles the two.
- `company/operating-model.md`'s five controlled loops have no executable counterpart for
  Checkpoint (budgets) or Learning.

---

## 3. What Has Actually Been Built

Legend: ✅ Completed · 🟡 Partial · ⛔ Not started · 🚧 Blocked · ❓ Unknown

| Component / Capability | Intended Purpose | Current Implementation | Status | Evidence | Missing / Incomplete | Dependencies / Blockers | Autonomy Impact |
|---|---|---|---|---|---|---|---|
| Workflow state machine | Governed DAG execution | 374 lines; append-only hash-chained events, idempotent mutations, retry/cycle caps, 6 terminal states | ✅ | `runtime/company_runtime.py`; 21 controlled-runtime checks pass | — | — | Foundation; sound |
| Cross-platform locking | Run the runtime anywhere | `fcntl` POSIX / `msvcrt` Windows, bounded polling | ✅ | `runtime/filelock.py`; 5/5 `test_filelock` | — | — | Unblocked local verification |
| Executor (agent invoker) | Turn ready steps into finished work | Claims step, dispatches to owner via `claude -p`, grades, writes hashed evidence, calls `complete` | ✅ | `runtime/executor.py`; 9 executor tests; `gold-auto-01/02` | Tools disabled; green path only for auto-advance | — | Core of autonomy; works |
| Agent tool access | Let departments touch real systems | **None** — `--allowedTools ""` | ⛔ | `runtime/backends.py:56` | Per-role tool allowlist, governed tool surface | AGENT-06, TOOL-08 | **Top blocker** — output is prose, not work |
| Maker-checker driving | Independent review without a human | `drive_check()`; unreadable verdict treated as RETURN | ✅ | `runtime/checking.py`; 7 tests; live `gold-mc-03` | — | — | Quality loop closed |
| Output validation | Reject stubs/refusals; grade vs. criteria | Structural gate + opt-in acceptance grader | ✅ | 6 gate tests; live `gold-graded-01` rejected then passed | Grader is same model family as author | — | Prevents junk advancing |
| Planner | Goal → validated workflow DAG | `runtime/planner.py`, 3 repair attempts against real validator | ✅ | 9 tests; live `fix-onboarding` = 18 steps / 10 depts | Plans are structural, not data-informed | Tool access | Goal intake works |
| Scheduler | Drive runs without a human command | `sweep()` / `serve()` / `watch()` / `mirror()`, bounded | 🟡 | `runtime/scheduler.py`; 6 sweep tests | No installed service; no daemon; must be launched by hand | DEP unit | Autonomy stops when the shell closes |
| Triggers (webhook / cron / SLA) | Let the world start work | **None wired** — verifier exists, no route | ⛔ | no `/webhooks` in `api.py` route table; no cron in code | Endpoint, event→run mapping, clock | HOOK-02/03/04 | Org is reactive-to-humans only |
| Human notification | Tell a person a gate is waiting | `runtime/notify.py` outbox + optional operator-wired command | ✅ | `runtime/runs/_outbox.jsonl` (1,428 B, populated) | Delivery command not configured; no SLA/age escalation | — | Silence problem solved locally |
| Approval console | Decide from a brief, not a wall of prose | `approval_server.py` + `briefing.py`, 5-line ASK/IF YES/FINDINGS/WATCH/RECOMMEND, ordered by blast radius | ✅ | 20 tests incl. ordering + XSS; `*.release-output.brief` files | Local, single operator, no auth | — | Human latency reduced |
| Approval UI (Control Center) | Approve/reject from the web app | Intake + UI-state only | ⛔ | `apps/control-center/app/control-center.tsx` | Approve/reject surface | API-02 | Gates are terminal-only |
| Agent-facing API | Out-of-process agents claim/submit work | `GET /v1/work`, `/v1/claim`, `/v1/submit`, `/v1/heartbeat`, `/v1/fail` | ✅ | `runtime/agent_api.py`; 22 tests | One shared bearer token; no per-agent identity | — | Enables external workers |
| Leases / liveness | Detect a hung step | `runtime/leases.py` grant/renew/release/expire | ✅ | 149 lines + tests | No lease file present (never exercised outside tests) | — | Recovery path exists |
| Run health / stall detection | Know what is stuck | `runtime/health.py`: running / waiting / stalled / finished / failed | ✅ | 7 health tests | No alerting — a person must look | OBS-02/03 | Self-monitoring, unnotified |
| Escalation | Raise what needs a person | `runtime/escalation.py` scan → notices | ✅ | 112 lines + module suite | Notices generated but drive no run (observed in prior session) | notify delivery | Partial |
| Memory (cross-run) | Don't re-solve the same problem | Append-only, hash-chained, propose→approve→recall into prompts | ✅ | 17 tests; live lesson crossed runs | Keyword recall only; 2 entries total | — | Working, barely used |
| Org state (goals/tasks/decisions) | Durable accountability outside runs | `scripts/org_state.py` written | ⛔ | `state/` = `README.md` only | Any adoption at all | — | Dead code today |
| State architecture | One answer to "what is ready?" | Log = execution record; SQLite = identity + read model; one-way `projection.py` | ✅ | 15 projection tests; migration `004` | `runtime/data/` absent here → store never created; Control Center still reads the old shape | — | Drift resolved by decision |
| Audit log writer | Tamper-evident "who approved what" | **None** | ⛔ | `logs/audit-log.jsonl` = 7 lines, 2026-07-14; no non-test writer | `runtime/audit.py` + call sites + behaviour test | — | **Governance is self-reported** |
| Connectors | Reach external systems | Admission control, authorization lifecycle, idempotency, receipts, webhook verification | 🟡 | `runtime/connectors.py`, `service.py`, migration 003 | Fixture-only gateway; no real HTTP/OAuth adapter | Human OAuth | Org has no hands |
| MCP / plugin config | Bind declared skills to real tools | **None in repo** | ⛔ | no `.mcp.json`, no `plugin.json` | Tool bindings for the 124 declared skills | ARCH-05 | Declared ≠ usable |
| HTTP service | Operator/API boundary | 16 routes, HMAC short-lived tokens, DB-bound roles, rate limit, headers, body cap | ✅ | `runtime/api.py`, `auth.py`, `db.py` | No run/step read routes; no OpenAPI | ARCH-01 | Sound |
| Test suite | Prove behaviour | 12 shell modules + ~39 Python test modules (218 checks + unit tests) | 🟡 | `bash tests/run.sh` → `SUITE: FAIL`, 5 failures | 3 governance-prose failures + 2 size failures; still ~majority grep-over-Markdown | DOC-07, SKILL-04 | Verification integrity |
| CI | Catch regressions | compileall, suite, CodeQL, SBOM, npm audit, release gate | 🚧 | `.github/workflows/ci.yml` | **The project is not a git repository** — CI has never run and nothing is committed | git init | No regression safety net |
| Deployment | Run it for real | systemd api/backup/maintenance units, reverse-proxy sample, env template, Cloudflare worker | 🟡 | `deploy/`, `apps/control-center/worker/index.ts` | No scheduler/executor unit; never deployed; no rollback drill | — | Nothing runs unattended |
| Python dependency manifest | Reproducible toolchain | **None** | ⛔ | no `requirements.txt` / `pyproject.toml` | Pin interpreter + pytest | — | Environment drift |

---

## 4. Autonomy Gap Analysis

**G1 — Agents have no hands. [V]**
*Missing:* any tool surface for a dispatched agent. `backends.py:56` passes
`--allowedTools ""`.
*Expected:* the CFO step queries the ledger; the CTO step reads the repo; the CRO step
writes to CRM through a governed connector.
*Actual:* every step returns text describing what would be done; the driver hashes that
text as "evidence".
*Why it matters:* an organization that cannot touch anything cannot do work — it can only
produce meeting minutes about work. Every downstream claim ("the company handled the
lead") is therefore unfalsifiable.
*Depends on it:* AGENT-06 per-role permissions, TOOL-03/04 live connectors, TOOL-08 MCP
config, WF-09 real workflows, PLAN data-informed planning.

**G2 — No trigger layer. [V]**
*Missing:* `/webhooks` route (verifier built but unreferenced by `api.py`), cron/calendar
triggers, SLA/threshold clocks, Claude Code hooks (`.claude/` has no hooks config).
*Expected:* an inbound lead, a failed payment, a T-90 renewal date, or a breached SLA
starts a run.
*Actual:* every run begins with a human typing `planner`/`executor`/`scheduler`.
*Why it matters:* exception-based autonomy requires the *system* to notice the exception.
Today the human is the sensor.
*Depends on it:* every revenue-engine and trust-compliance skill whose value is timeliness.

**G3 — The loop is not a service. [V]**
*Missing:* a supervised long-running process. `scheduler.serve()` is bounded by
`max_passes`; `deploy/` has no scheduler or executor unit.
*Actual:* autonomy exists only while a terminal is open.
*Why it matters:* unattended operation is the definition of the target.

**G4 — Audit evidence is produced by the audited actor. [V]**
*Missing:* `runtime/audit.py` and call sites. REV1's RCA-5 prescribed exactly this and it
was not built; the tracker ID was reused for stall detection, hiding the omission.
*Actual:* `logs/audit-log.jsonl` unchanged since genesis; the `audit-log` skill instructs
an *agent* to append via an ad-hoc heredoc; tests assert the skill *text* mentions logging.
*Why it matters:* under unattended execution, a skipped gate leaves no trace and the
absence is itself invisible. The correct pattern already exists in `db.py:186-210`
(`operational_events` hash chain) — it is simply not applied to run governance.

**G5 — The operator read model has never existed on this host. [V]**
`runtime/data/` does not exist; `projection.py` mirrors "only when a store is configured".
*Why it matters:* `waiting_on_humans()`, the Control Center, and any multi-user view are
untested against real data. The one-way projection decision is sound but unexercised
end-to-end.

**G6 — Org state is dead code. [V]** `state/` holds only `README.md`. Goals, owned tasks
and decisions — the accountability layer named in `CLAUDE.md` §6 — have never been
recorded. Runs are the only durable unit of work, so nothing survives above the run.

**G7 — Declared capability is not usable capability. [V]** 124 of 137 skill references are
declared with `verified_here: false`; there is no `.mcp.json` or `plugin.json` binding any
of them. The manifest converted an unknown into a known unknown — real progress — but a
department that "owns" `anthropic-skills:xlsx` still cannot open a spreadsheet.

**G8 — Verification is drifting again. [V]** The suite is red on 5 checks; REV1 records it
as green but for two. Three of the five are governance-prose assertions (DSR send gating,
breach-notification gating, per-send demand-gen gating) — meaning documented governance
text no longer matches what the tests demand. Because ~most of the 218 checks are
`grep`-over-Markdown, a green suite does not imply a working product, and a red suite is
now being carried forward silently.

**G9 — No version control. [V]** The working directory is not a git repository. CI exists
but has never run; the four live gold runs are gitignored artifacts; no change is
reviewable or revertible.

**G10 — No run-level observability or alerting. [V]** `/metrics` is HTTP-only; no metrics
on step latency, retry rate, approval wait, escalation rate; alerts cover API health only.
Health and stall detection exist as commands a person must run.

**G11 — Approval identity is self-asserted. [V]** `decide()` requires a non-empty approver
name and reason, and both land in the event chain — but nothing authenticates the name.
Under autonomy the approver is the only real control at yellow.

**G12 — Grader and author share a model family. [V]** Acceptance grading and maker-checker
review are performed by the same underlying model as the producer. Correlated blind spots
are not mitigated by any independent signal.

---

## 5. Complete Project Tracker

Status: ✅ done · 🟡 partial · ⛔ not started · 🚧 blocked on a human/external · ❓ unknown.
IDs continue REV1 where the item is the same, so history stays referenceable. New IDs are
marked **(new)**.

### 5.1 Execution spine

| ID | Deliverable | Status | What Exists | What Is Missing | Deps / Blockers | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| ARCH-01 | One state architecture | ✅ | Log = execution SoR; SQLite = identity + read model; one-way projection | Control Center reads old shape | — | P1 | `projection.py`, mig 004, 15 tests |
| ARCH-02 | Cross-platform runtime | ✅ | `filelock.py` POSIX/Windows | — | — | — | 5/5 `test_filelock` |
| ARCH-06 **(new)** | Store instantiated on a real host | ⛔ | Migrations + code | `runtime/data/` never created; projection unexercised outside tests | — | P1 | `ls runtime/data` → absent |
| WF-01…07,12 | Schema, DAG, hash chain, idempotency, caps, evidence, terminal states | ✅ | All present | — | — | — | `company_runtime.py` |
| WF-08 | Goal → workflow | ✅ | `planner.py`, validator-checked, 3 repairs | Not data-informed | G1 | P1 | 9 tests; `fix-onboarding` |
| WF-09 | Workflow library | ⛔ | 2 gold-run proofs | Real business workflows | WF-08, G1 | P1 | `runtime/workflows/` |
| WF-10 | Org state adoption | ⛔ | `org_state.py` written | Any use at all | — | P1 | `state/` = README |
| WF-11 | Cross-run prioritization | ⛔ | Nothing | Queue ordering | ARCH-06 | P2 | — |
| AGENT-05 | Executor | ✅ | Claim → dispatch → grade → evidence → complete | Tools disabled | G1 | — | 9 tests; `gold-auto-01/02` |
| AGENT-08 | Maker-checker driving | ✅ | `drive_check()` | — | — | — | `gold-mc-03` |
| AGENT-09 | Handoff payload | ✅ | Hash-verified upstream evidence | Direct edges only (deliberate) | — | — | 5 handoff tests |
| LOOP-02 | Execution loop | ✅ | `advance()` bounded | — | — | — | `test_the_driver_never_loops_forever` |
| LOOP-03 | Scheduled sweep | 🟡 | `scheduler.sweep/serve` | Not installed as a service | DEP-07 | **P0** | 6 sweep tests; no unit in `deploy/` |
| LOOP-04 | Feedback loop | ✅ | Checker reasons → next attempt | Run-level feedback | — | P2 | `gold-mc-02` |
| LOOP-05 | Learning loop | 🟡 | Auto-proposes a lesson on RETURN/REJECT | Signal capture beyond checker verdicts | MEM-06 | P2 | `memory.py` |
| LOOP-06 | Checkpoint / budget loop | ⛔ | Documented caps only | Time/cost budget enforcement | — | P2 | `operating-model.md` §4 |

### 5.2 Reaching the world

| ID | Deliverable | Status | What Exists | What Is Missing | Deps / Blockers | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| EXEC-01 **(new)** | Governed tool access for dispatched agents | ⛔ | `--allowedTools ""` | Per-role allowlist, tool audit, evidence from real actions | AGENT-06, TOOL-08 | **P0** | `backends.py:56` |
| AGENT-06 | Per-role tool permissions | ⛔ | All-or-nothing | Least-privilege map role → tools | EXEC-01 | **P0** | agent files carry no tool list |
| HOOK-01 | Claude Code hooks | ⛔ | None | PreToolUse/PostToolUse enforcement of §3 | EXEC-01 | P1 | `.claude/` has no hooks |
| HOOK-02 | Inbound webhook trigger | ⛔ | `WebhookVerifier` built, unwired | Route + event→run mapping | ARCH-06 | **P0** | no `/webhooks` in `api.py` |
| HOOK-03 | Cron / calendar triggers | ⛔ | Prose in `CLAUDE.md` §5 | In-product schedule store + firing | LOOP-03 | **P0** | no cron in code |
| HOOK-04 | SLA / threshold triggers | ⛔ | SLA described in `lead-response` | Clock + breach event | HOOK-03 | P1 | — |
| TOOL-03 | Real execution gateway | 🟡 | Fixture only, refuses non-fixture | HTTP/OAuth adapter | TOOL-02 | P1 | `FixtureConnectorGateway` |
| TOOL-04 | Live connectors | 🚧 | `fixture.invalid` | Human OAuth authorization | TOOL-03 | P1 | ledger 0.1 |
| TOOL-07 | Webhook ingestion route | ⛔ | Verifier only | Endpoint | HOOK-02 | P1 | — |
| TOOL-08 | MCP / plugin config | ⛔ | None | `.mcp.json` binding declared skills | ARCH-05 | P1 | repo has neither |
| ARCH-05 | Declared external dependencies | 🟡 | `company/skills.manifest.json`, 124 declared, 0 unresolved | All `verified_here: false`; nothing binds them | TOOL-08 | P1 | `skills.summary()` |

### 5.3 Governance & trust

| ID | Deliverable | Status | What Exists | What Is Missing | Deps / Blockers | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| HITL-01 | Green/yellow/red gates | ✅ | Red unapprovable by any code path | — | — | — | `request_step():189-191` |
| HITL-02 | Approval console + briefs | ✅ | 5-line brief, blast-radius ordering | Local, unauthenticated | — | P1 | 20 tests |
| HITL-04 | Approval UI in Control Center | ✅ | The queue screen now reads `GET /v1/decisions` and posts a decision with a required reason. Red steps render as handed back and cannot be actioned. Overview shows the same live counts. The hard-coded mock run and the dead "Preview approve" buttons are gone | Single-operator; no run detail/timeline view yet | — | ~~P1~~ done | 29 tests incl. real HTTP; `module-decisions.sh` |
| HITL-06 | Approval attributable | 🟡 | Name + reason required, in event chain | Identity not authenticated | PROD-02 | P1 | `decide()` |
| AUD-01 **(new, was OBS-05 in REV1)** | Audit-log writer | ✅ | `runtime/audit.py` — nine validated fields, hash-chained, fsynced, under a file lock; `verify` and `tail` CLI. Pre-chain lines are sealed by an anchor digest rather than rewritten. The log follows `MYORG_RUNS_DIR`, so a test can never append to the company record | Only gate transitions are wired; connector execution and SLA events still self-report | — | ~~P0~~ done | 14 tests; live run `live-audit` produced 3 chained entries with no agent involved |
| AUD-02 **(new)** | Gated actions produce a log line (test) | ✅ | `tests/test_audit.py` (14) + `module-audit-log.sh` L7: the module now asserts the runtime *produces* entries and that the chain verifies, not that the skill text mentions logging | — | AUD-01 | ~~P0~~ done | audit-log module 28 → 32 passed / 0 failed |
| HITL-07 | Prompt-injection stance | 🟡(doc) | Constitution + agent charters | Runtime enforcement | HOOK-01 | P1 | `CLAUDE.md` §3 |
| SEC-07 | Least privilege at run time | ⛔ | — | Follows EXEC-01/AGENT-06 | EXEC-01 | P1 | — |
| VAL-02 | Output quality validation | ✅ | Structural gate + acceptance grader | Grader shares model family | — | P2 | `gold-graded-01` |
| VAL-06 **(new)** | Independent-signal validation | ⛔ | — | Non-model check (schema, data, external truth) for at least one action class | EXEC-01 | P2 | — |

### 5.4 Recovery & observability

| ID | Deliverable | Status | What Exists | What Is Missing | Deps / Blockers | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| REC-02 | Leases / liveness | ✅ | grant/renew/release/expire | Never exercised outside tests | — | P2 | `leases.py` |
| REC-08 | Approved steps get finished | ✅ | `advance()` drives `in_progress` | — | — | — | test present |
| REC-09 **(new)** | Escalation drives work | 🟡 | `escalation.scan()` raises notices | Notices did not move stalled runs until the scheduler fix; unverified since | LOOP-03 | P1 | prior-session observation |
| OBS-04 | Run health | ✅ | running/waiting/stalled/finished/failed | — | — | — | 7 tests |
| OBS-07 **(new)** | Stall/approval alerting | ⛔ | Detection only | Alert route + thresholds | OBS-02 | P1 | `health.py` |
| OBS-02 | Run/step metrics | ⛔ | HTTP metrics only | Step latency, retry rate, approval age, escalation rate | ARCH-06 | P1 | `observability.py` |
| OBS-03 | Alert rules | 🟡 | API health only | Stuck-run, approval-age rules | OBS-02 | P1 | `prometheus-alerts.yml` |
| NOTIFY-01 **(new)** | Human notification outbox | ✅ | `notify.py` + `_outbox.jsonl` | No delivery command configured; no age escalation | — | P1 | 1,428 B outbox |

### 5.5 Memory & knowledge

| ID | Deliverable | Status | What Exists | What Is Missing | Deps / Blockers | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| MEM-05 | Context propagation | ✅ | Hash-verified upstream evidence | — | — | — | tests |
| MEM-06 | Cross-run memory | ✅ | Propose → approve → recall | Keyword recall; 2 entries; no decay | — | P2 | `memory/default.memory.jsonl` |
| MEM-04 | Org state adoption | ⛔ | `org_state.py` inert | Use by executor/planner | WF-10 | P1 | `state/` = README |
| MEM-02 | Business memory (facts) | 🟡 | Same store, unused for facts | Ingest path | MEM-06 | P2 | 2 entries |

### 5.6 Engineering & delivery

| ID | Deliverable | Status | What Exists | What Is Missing | Deps / Blockers | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| VCS-01 **(new)** | Version control | ⛔ | `.github/workflows/ci.yml` | **Not a git repository** — CI has never run | — | **P0** | env check; REV1 TEST-06 note |
| TEST-01 | Suite green | ⛔ | 218 checks + unit modules | 5 failures: 2 × `CLAUDE.md` size, demand-gen per-send, DSR send, breach notify | DOC-07, SKILL-04 | **P0** | `bash tests/run.sh` |
| SKILL-04 **(new ID for the 3 prose failures)** | Governance text matches tests | ⛔ | Skill docs | Restore per-send gating wording in `demand-gen`, `privacy-program` | — | P1 | 3 failing checks |
| TEST-02 | Behaviour vs. prose ratio | 🟡 | Real behaviour tests now exist for executor/planner/scheduler/memory/health | Majority of checks still grep Markdown | — | P1 | suite inspection |
| TEST-05 | Cross-platform CI | ⛔ | ubuntu-24.04 only, never executed | windows-latest matrix | VCS-01 | P1 | `ci.yml` |
| DEP-06 | Python dependency manifest | ⛔ | None | `requirements.txt`/`pyproject.toml`, pin pytest | — | P1 | repo root |
| DEP-07 **(new)** | Scheduler/executor service unit | ⛔ | api/backup/maintenance units | Supervised loop unit + Windows equivalent | LOOP-03 | **P0** | `deploy/` |
| DOC-07 | `CLAUDE.md` within guardrail | ⛔ | 222 lines / 12,592 B | Trim, or raise the cap deliberately | — | P1 | `core.sh` |
| DOC-08 **(new)** | Docs state the target vs. today | ⛔ | README/RUNTIME-AUDIT describe HITL as the goal | One line stating the settled autonomy target and that HITL is the current stage | — | P2 | `docs/RUNTIME-AUDIT.md` |
| API-02 | Run/step read routes | 🟡 | `GET /v1/decisions` and `POST /v1/decisions/{run}/{step}` serve the human queue, org-scoped, `decision-owner` + human identity required, mirrored into `operational_events` | Full run/step listing and timeline still unexposed | ARCH-06 | P1 | `tests/test_decisions.py` |
| API-06 | OpenAPI spec | ⛔ | None | Machine-readable contract | — | P2 | — |
| DEBT-02 **(new)** | Oversized modules | 🟡 | `executor.py` split to 278 | `db.py` 777, `api.py` 411, `company_runtime.py` 374 exceed the 300-line house rule | — | P2 | `wc -l runtime/*.py` |
| PROD-02 | Production identity | 🚧 | Local HMAC tokens | Managed IdP | — | P1 | ledger OS-7 |
| PROD-04/05 | UAT + deploy/rollback drill | ⛔ | Runbooks only | Execution | VCS-01 | P2 | `docs/UAT-*.md` |

---

## 6. Critical Path

### P0 — Autonomy blockers

| # | Blocker | Tracker IDs | Why it is P0 | Unlocks |
|---|---|---|---|---|
| ~~0~~ | ~~Two drivers can do the same step~~ | REC-10 | **CLOSED 2026-09-01.** Ownership is a precondition of the write, not a courtesy | Safe multi-replica workers; unblocks DEP-07 and the Docker migration |
| ~~0~~ | ~~Quality gates fail open~~ | VAL-07 | **CLOSED 2026-09-01.** A control that cannot run no longer reports a pass | Trustworthy unattended grading; safe multi-replica operation later |
| ~~1~~ | ~~Governance evidence is self-reported~~ | AUD-01, AUD-02 | **CLOSED 2026-09-01.** `runtime/audit.py` writes the line as a side effect of the gate; the transition fails closed if the log cannot be written. RCA-A's root cause — control and evidence on the same side of the trust boundary — is removed for gate transitions | Trustworthy unattended operation; HITL-05; every governance claim in the ledger |
| 2 | Agents cannot use tools | EXEC-01, AGENT-06 | Output is prose about work, not work. Every department capability is currently unfalsifiable. | Real deliverables, data-informed planning, live connectors, real workflows |
| 3 | Nothing can start work but a person | HOOK-02, HOOK-03, HOOK-04, TOOL-07 | Exception-based autonomy requires the system to notice the exception. | Revenue-engine and compliance skills whose value is timeliness |
| 4 | The loop is not a supervised service | LOOP-03, DEP-07 | Autonomy currently lasts as long as a terminal stays open. | Unattended operation; SLA clocks; stall alerting |
| 5 | No version control | VCS-01 | No review, no revert, no CI; the safety net for every change above does not exist. | CI, regression safety, evidence that survives |
| 6 | Suite is red while the tracker says green | TEST-01, SKILL-04, DOC-07 | Verification integrity: a red suite carried forward silently is how REV1's drift happened. | Trustworthy "done" |

### P1 — Major capability gaps

ARCH-06 store instantiation · API-02 run/step routes · HITL-04 approval UI ·
HITL-06 authenticated approver · TOOL-03/04/07/08 live connectors + MCP bindings ·
OBS-02/03/07 run telemetry and alerting · NOTIFY-01 delivery + approval-age escalation ·
MEM-04/WF-10 org-state adoption · WF-09 real workflow library · REC-09 escalation drives
work · TEST-02/05 behaviour coverage + Windows CI · DEP-06 dependency manifest ·
PROD-02 managed identity · HOOK-01 hooks enforcement.

### P2 — Hardening

LOOP-06 budgets · VAL-06 independent-signal validation · DEBT-02 module sizes ·
API-06 OpenAPI · MEM-02/06 recall quality · DOC-08 target-vs-today statement ·
DEP-04/05 deploy + rollback drills · PROD-04 UAT · WF-11 prioritization.

### Dependency order

```
VCS-01 git init ──► TEST-01 suite green (SKILL-04, DOC-07)
   │                     │
   └──► AUD-01/02 audit writer ◄──┘
                │
                ├──► EXEC-01 tool access ──► AGENT-06 per-role permissions ──► HOOK-01
                │           │
                │           └──► TOOL-03/04 live connectors ──► WF-09 real workflows
                │
                ├──► DEP-07 scheduler service ──► HOOK-03 cron ──► HOOK-04 SLA clocks
                │                                      │
                │                              HOOK-02/TOOL-07 webhook route
                │                                      │
                └──► ARCH-06 store ──► API-02 ──► HITL-04 approval UI ──► OBS-02/07
```

**Parallelisable now:** VCS-01, AUD-01, SKILL-04+DOC-07, DEP-06 are mutually independent.
EXEC-01 and DEP-07 are independent of each other. ARCH-06 is independent of both.

---

## 7. Root-Cause Analysis — P0 Blockers

### RCA-A — Governance evidence is produced by the audited actor (AUD-01/02)

- **Problem:** No code appends to `logs/audit-log.jsonl`; the gate record is whatever an
  agent chose to write.
- **Why 1:** `grep -rn 'audit-log.jsonl'` over `.py/.ts/.sh` outside `tests/` → zero hits. [V]
- **Why 2:** The `audit-log` skill prescribes an ad-hoc `python3 - <<'EOF'` heredoc for the
  agent to run. [V]
- **Why 3:** Logging was designed as a *behaviour the model performs*, matching the
  repo-wide governance-as-prompt pattern. [V]
- **Why 4:** REV1 identified exactly this (RCA-5) and prescribed `runtime/audit.py`; the
  fix was not built, and the ID `OBS-05` was reassigned to stall detection. [V]
- **Why 5 (root):** **Tracker identity is not stable, so an unfixed item can leave the
  tracker by being renamed.** Combined with control and evidence sitting on the same side
  of the trust boundary, the omission became invisible twice over. [I]
- **Contributing:** No append API; tests assert skill *text*, not produced lines; the
  correct pattern (`db.py:186-210` hash chain) exists but was never applied to runs.
- **Consequences:** No reliable "who approved what, when" under unattended execution.
- **Corrective:** `runtime/audit.py` — validated fields, hash-chained like
  `_append_operational_event`, fsync; called from `request_step` (yellow/red),
  `approve`/`reject`, and every terminal transition. Keep the skill as the read interface.
- **Preventive:** Freeze tracker IDs — a retired item is struck through, never reused; add
  a behaviour test that a gated action *produces* a chained line.
- **Verification:** Drive a run to its yellow step with no agent instructed to log; a valid
  chained entry appears; mutating any byte breaks chain verification.
- **Affects:** AUD-01, AUD-02, HITL-05, and every governance claim in `docs/GAP-LEDGER.md`.

### RCA-B — Agents cannot use tools (EXEC-01, AGENT-06)

- **Problem:** Dispatch disables all tools, so no step touches a real system.
- **Why 1:** `backends.py:56` passes `--allowedTools ""`. [V]
- **Why 2:** The docstring states the reason: "the driver is the only thing that writes to
  the run… keeps every side effect inside the governed state machine." [V]
- **Why 3:** The state machine's integrity model is *evidence = hash of the agent's text*;
  an agent that writes files would create side effects the chain cannot see. [V]
- **Why 4:** There is no per-role permission model to constrain what a tool-enabled agent
  could do — `AGENT-06` was never built, and agent files carry no tool lists. [V]
- **Why 5 (root):** **Integrity was achieved by removing capability rather than by scoping
  it.** The safe design and the useful design were never reconciled, because under the
  original human-in-the-loop target the human supplied all the capability. [I]
- **Contributing:** No MCP bindings (TOOL-08); connectors are fixture-only (TOOL-03); no
  hooks to enforce §3 at tool-call time (HOOK-01).
- **Consequences:** Every department capability is a claim; planning cannot be
  data-informed; the revenue and compliance skills cannot operate on real data.
- **Corrective:** Introduce a scoped tool surface: (a) declare per-role allowed tools in
  agent frontmatter, (b) pass that allowlist to `claude -p` instead of `""`, (c) record
  tool calls into the run's event chain so side effects remain visible to the state
  machine, (d) keep yellow/red actions unreachable by tools — they stay gated.
- **Preventive:** A test that a role cannot invoke a tool outside its allowlist, and that
  every tool call appears in the run's event chain.
- **Verification:** A run whose CTO step reads a real file produces evidence containing
  data that exists only in that file, and the event chain shows the read.
- **Affects:** EXEC-01, AGENT-06, SEC-07, HOOK-01, TOOL-03/04/08, WF-08 (data-informed),
  WF-09, VAL-06.

### RCA-C — Nothing can start work but a person (HOOK-02/03/04, DEP-07, LOOP-03)

- **Problem:** No trigger layer and no supervised loop; a run starts only when a human
  types a command.
- **Why 1:** No `/webhooks` route in `api.py`; `WebhookVerifier` is referenced by nothing
  outside tests. No cron/schedule store in code. No scheduler unit in `deploy/`. [V]
- **Why 2:** `scheduler.serve()` is bounded by `max_passes` — designed as a foreground
  command, not a daemon. [V]
- **Why 3:** The scheduler was built to close the "no automatic advance" P0, which is
  about *driving existing runs*, not about *creating* them. Creation was never in scope. [I]
- **Why 4 (root):** **The system models autonomy as "finish work faster" rather than
  "notice work and start it".** The human-as-sensor assumption survived the executor
  work because nothing tested for it. [I]
- **Contributing:** `CLAUDE.md` §5 delegates cadences to Claude Code's external `schedule`
  skill — outside this repo, so the product never needed its own.
- **Consequences:** Throughput is capped by human attention; every SLA-shaped skill
  (`lead-response`, `ar-collections`, `renewals-retention`, `privacy-program` clocks) is
  advisory only.
- **Corrective:** (a) a supervised `serve` unit for POSIX and Windows, (b) a `/webhooks`
  route mapping a verified event to `planner.plan()` + a run, (c) a minimal schedule store
  firing named workflows, (d) SLA clocks that emit a breach event.
- **Preventive:** An acceptance test that asserts a run exists that no human created.
- **Verification:** With no terminal open, a posted signed webhook produces a run that
  reaches its first yellow gate and a notice in the outbox.
- **Affects:** HOOK-02/03/04, TOOL-07, DEP-07, LOOP-03, OBS-07, REC-09.

### RCA-D — No version control, red suite carried forward (VCS-01, TEST-01)

- **Problem:** The project is not a git repository; CI has never executed; the suite is red
  on 5 checks while the tracker records it as green but for two.
- **Why 1:** `git` reports no repository at `C:\AgenticAI\MyOrg`. [V]
- **Why 2:** `.github/workflows/ci.yml` exists and is well-formed, so CI was designed but
  never connected. [V]
- **Why 3:** Because there is no CI and no commit boundary, the only regression signal is a
  human running `tests/run.sh` and reading the tail — and the tail shows the last module,
  not the totals. [V]
- **Why 4 (root):** **"Done" is recorded in a document rather than enforced by a gate.**
  The tracker is the source of truth for status, and the tracker is hand-edited. [I]
- **Contributing:** No dependency manifest, so the Python tests' environment is not
  reproducible; ~most checks grep Markdown, so a green suite was never strong evidence.
- **Consequences:** Documentation drifts ahead of code — the exact failure REV1 diagnosed,
  recurring within one day.
- **Corrective:** `git init` + first commit of the current tree; make CI the gate that
  writes status; fix the 3 governance-text failures and settle the `CLAUDE.md` cap.
- **Preventive:** No tracker item may be marked ✅ without a named passing test.
- **Verification:** CI runs on a commit and the suite exits 0.
- **Affects:** VCS-01, TEST-01/02/05, SKILL-04, DOC-07, DEP-06, and the reliability of
  every status in this document.

---

## 8. Recommended First Fix

**Build the audit writer: AUD-01 + AUD-02, preceded by `git init` (VCS-01).**

**Why first.** The executor already runs steps without a human present. Every other P0
adds *more* unattended behaviour — tools, triggers, a daemon. Adding capability before the
tamper-evident record means each new power lands with no way to prove it was exercised
within policy. AUD-01 is also the smallest P0 (one module, four call sites, one test), it
changes no existing behaviour, the correct pattern already exists in the codebase to copy
(`db.py:186-210`), and REV1 already specified it — it is unfinished work, not new design.
`git init` comes with it because a fix to the governance backbone should be the first thing
that is reviewable and revertible.

**Files likely involved**
- New: `runtime/audit.py` (append, chain, verify), `tests/test_audit.py`.
- Edit: `runtime/company_runtime.py` — `request_step()` (yellow → `awaiting_approval`,
  red → `blocked_human`), the approve/reject path, and terminal transitions.
- Edit: `tests/module-audit-log.sh` — replace the text-grep with a behaviour assertion.
- Read-only reference: `runtime/db.py:186-210` (`_append_operational_event`).

**Desired end state.** Every gate transition and every approval decision appends a
validated, hash-chained, fsynced line to `logs/audit-log.jsonl` as a *side effect of the
runtime*, with no agent choosing to do it. The `audit-log` skill remains the read/review
interface.

**Acceptance criteria**
1. Driving a run to a yellow step appends exactly one entry with actor, action, category,
   target, approval ref, evidence hash, outcome, timestamp, and `prev_hash`.
2. Approving that step appends a second entry naming the approver and reason.
3. A red step appends an entry recording the hand-back; no entry ever records a red
   approval.
4. Mutating any byte of any line makes chain verification fail loudly.
5. The 7 pre-existing hand-authored lines are handled explicitly — either migrated into
   the chain at genesis or moved to `logs/audit-log.legacy.jsonl`; the choice is recorded.
6. No agent prompt is changed; no step output is required to mention logging.
7. `bash tests/run.sh` shows the audit-log module green, and no previously passing check
   regresses.

**Verification.** Run `gold-auto-*`-shaped workflow to its yellow step with the
`StubBackend` (no tokens spent), then assert entries 1–4 above; plus one live run to
confirm nothing about real dispatch changes.

**Do not change yet**
- `--allowedTools ""` (EXEC-01) — it needs the per-role permission model first, and it must
  land *after* the audit trail exists.
- The JSONL/SQLite split — settled deliberately; do not migrate.
- Trigger/webhook routes and the scheduler service — separate P0s with their own tests.
- `CLAUDE.md` size and the 3 governance-text failures — real, but independent; fix in
  parallel, not inside this change.

---

## 9. Questions / Evidence Needed

| # | Cannot be determined | Why | Evidence that would resolve it | On critical path? |
|---|---|---|---|---|
| 1 | Whether the 124 declared external skills actually exist in the environment the agents run in | `verified_here: false` by design; the manifest is a declaration, and nothing has invoked them | One invocation attempt per family, recorded | Yes — gates EXEC-01's usefulness |
| 2 | Whether the Control Center works against a live API | `runtime/data/` has never existed here; the worker has never been deployed | One local run of the store + UI against `api.py` | Yes — gates HITL-04 |
| 3 | Whether `escalation.scan()` now moves stalled runs | Fixed late in the prior session; no run has stalled since | A deliberately stalled run, swept, then re-checked | Partly — REC-09 |
| 4 | Intended production host and identity provider | `deploy/` is systemd-only; the dev host is Windows | A stated deployment target | Yes — shapes DEP-07 |
| 5 | Whether the 3 governance-text failures are regressions or deliberate policy edits | No version control, so no diff exists | `git init` + the author's intent | Yes — TEST-01 |

---

## 10. Baseline commands run for REV2

```
find . -type f | wc -l ; wc -l runtime/*.py scripts/*.py
bash tests/run.sh                                  # SUITE: FAIL — 5 failing checks
python -c "from runtime import skills; print(skills.summary())"
                                                   # 17 depts, 151 refs, 137 distinct,
                                                   # 124 declared, 13 local, 0 unresolved
ls -A state/ memory/ runtime/data logs/            # state=README only; runtime/data absent
wc -l logs/audit-log.jsonl memory/default.memory.jsonl   # 7 (unchanged), 2
grep -rn 'audit-log.jsonl' --include=*.py --include=*.ts --include=*.sh .   # tests only
grep -n 'allowedTools' runtime/backends.py         # --allowedTools ""
grep -n '/v1/\|/healthz\|/metrics' runtime/api.py  # 16 routes, no /webhooks
ls deploy/                                         # api, backup, maintenance; no scheduler
ls runtime/runs/*.jsonl | wc -l                    # live runs incl. 4 gold runs
```

---

# Investigation Cycle 1 — bidirectional failure analysis (2026-09-01, later same day)

Scope: the five action items agreed after REV2 (VCS-01, AUD-01/02, AGENT-06→EXEC-01,
HOOK-02/03 + DEP-07, SKILL-04 + DOC-07). Method: for each capability on that path, probe
both directions — why it fails, and how it could fail *while appearing to succeed*.
All **[RV]** findings below were reproduced by executing the runtime with `StubBackend`
against an isolated `MYORG_RUNS_DIR`; the probes are summarised in §11.1. Static-only
findings are marked **[V-static]**.

## 11. Investigation Ledger

| ID | Question / Hypothesis | Tracker | Evidence | Finding | Confidence | Remaining Unknown | Next Action | Status |
|---|---|---|---|---|---|---|---|---|
| I-01 | Does a retry reuse `request_id` and get silently swallowed as an idempotent replay? | WF-04 | `executor.request_id()` = `exec-<step>-<uuid12>`; 5 calls → 5 distinct values **[RV]** | **Hypothesis wrong**, but the inverse is true: replay protection can *never* fire on the autonomous path, so a crash-and-resweep re-dispatches and re-spends | High | Whether CLI operators reuse ids in practice | Fold into the REC-10 fix: derive the id from (run, step, attempt) | Resolved → new item WF-13 |
| I-02 | If the acceptance grader is unavailable, is work still gated? | VAL-02 | Probe P4: grader raised on every call; step completed, run completed, log said "could not grade — accepting unscored" **[RV]**. `agent_api.graded_failure` catches bare `Exception` → `None` **[V-static]** | **The quality gate fails open.** A transient `claude` failure silently turns a graded step into an ungraded one | High | Real-world outage rate of the CLI backend | Make the gate fail closed — park, do not pass | Resolved → new item VAL-07 |
| I-03 | Do leases actually stop two drivers doing the same step? | REC-02, AGENT-05 | Probe P5: an external worker claimed `s1` and held the lease; `advance()` dispatched and completed the same step anyway; the worker's `submit` was then rejected with "run is terminal" **[RV]**. `grep leases` → used only in `agent_api.py` and `scheduler.reclaim` **[V-static]** | **Leases are advisory and one-sided.** The in-process driver never checks or takes one, so work is duplicated, model spend doubles, and the external agent's finished output is discarded with no record | High | Whether two concurrent `sweep()` processes collide the same way (same code path — expected, untested) | Make `complete`/`fail` require the holder's token | Resolved → new item REC-10 |
| I-04 | What happens when a run exhausts `max_cycles` mid-flight? | WF-05 | Probe P3: run went `blocked_cycle_limit` with `s1`,`s2` completed and `s3` ready; re-driving returned **without error and without progress** **[RV]** | Terminal, unresumable, and the silent no-op re-drive makes an operator retry look like success | High | Measured cost is 2 cycles/plain step and 3/checked step against a 4/step planner budget, so the happy path has headroom; retries and long runs do not | Add a resumable budget extension; make re-drive of a terminal run say so | Resolved → new item REC-11 |
| I-05 | Is a stopped run actually reported to a human, or does it die quietly? | OBS-04, NOTIFY-01 | Probe 2: budget-dead run → `health` = `failed`; yellow and red gates → `waiting on you`; `escalation.scan()` raised exactly 3 notices and **0 on rescan**; the scheduler correctly excluded all three from `movable_runs()` **[RV]** | **This layer works.** Detection, classification, dedupe and queueing are correct — better than REV1 assumed | High | — | None | Resolved — no defect |
| I-06 | Does `notify.deliver()` distinguish "delivered" from "nothing configured"? | NOTIFY-01 | Probe 2: with no `MYORG_NOTIFY_COMMAND`, `deliver()` returned 3 notices while `outstanding()` still returned 3 **[RV]** | Same return value for "sent 3" and "sent nothing" — a caller or operator reading the count is misled | Medium | Whether any caller relies on the return value | Return delivered-vs-queued explicitly | Resolved → new item NOTIFY-02 |
| I-07 | Is `git init` safe, and does the first commit preserve the evidence the audit relies on? | VCS-01 | `.gitignore` excludes `runtime/runs/*.jsonl`, `*.evidence`, `*.workflow.json`, `runtime/data/*.db` **[V-static]**; `git rev-parse` → not a repository **[RV]** | Committing now is safe but **captures no proof the system ever ran** — the four gold runs stay unversioned | High | Whether a secret sits in any tracked path (a scanner exists: `scripts/release_evidence.py`) | Run the secret scan, then commit; curate one gold run into `examples/` | Resolved → new item VCS-02 |
| I-08 | Can per-role tool allowlists be expressed at all today? | AGENT-06, EXEC-01 | `grep -l '^tools:' .claude/agents/*.md` → **0 of 17** **[RV]** | There is no declaration surface for role tools; EXEC-01 cannot be scoped until one exists | High | Whether `claude -p` honours a per-invocation allowlist in this version | Verify the CLI flag contract before building | Open — blocks EXEC-01 |
| I-09 | Are the 3 governance-text failures regressions or deliberate edits? | SKILL-04 | The tests grep for exact prose: `every send stays 🟡 per-send`, `sending is 🟡`, `sends are 🟡, human-approved` **[V-static]** | The skill files no longer contain those phrases. Without version control there is no way to tell an intentional rewrite from an accidental one | High | The author's intent | Fix after `git init`, so the change is reviewable | Blocked on VCS-01 |
| I-10 | Does the yellow/red gate hold under the driver, not just under tests? | HITL-01 | Probe 2: `publish` → `awaiting_approval`; `move_money` → `blocked_human` with `run_status=blocked_human`; the driver stopped at both **[RV]** | **Holds.** The authority boundary is enforced in code on the autonomous path | High | — | None | Resolved — no defect |

### 11.1 Probes executed

| Probe | What it did | Result |
|---|---|---|
| P1 | 3 plain green steps, stub backend | completed, 6/12 cycles → **2.0 cycles/step** |
| P2 | 1 checked + 1 plain step | completed, 6/12 cycles → a checker costs ~1 extra |
| P3 | 3 steps, `max_cycles=4` | `blocked_cycle_limit` at 4/4, 2 steps stranded, re-drive a silent no-op |
| P4 | step with `acceptance`, grader raises on every call | **completed unscored** |
| P5 | external claim + lease, then in-process `advance()` | **both drove the step**; the worker's submit was rejected afterwards |
| P6 | `request_id()` determinism | 5 distinct ids → replay protection unreachable |
| Probe 2 | budget-dead + yellow + red runs → health, scheduler, escalation, outbox, deliver | detection and dedupe correct; `deliver()` ambiguous |

## 12. Failure Boundaries — P0/P1 capabilities

| Item | Current Result | Success Boundary | Failure Boundary | Root Cause / Constraint | Detection | Recovery | Evidence | Test Gap | Required Work |
|---|---|---|---|---|---|---|---|---|---|
| VAL-02 acceptance grading | **Fails closed as of VAL-07** | Grader answers within 3 attempts | Grader still unreachable → step parks at `awaiting_approval`, work kept, human decides | — | Audit line + `needs_approval` notice + console reason | Human approves → step completes with the stored evidence, no re-dispatch | `tests/test_grading.py` **[RV]** | — | ~~VAL-07~~ done |
| REC-02 leases / step ownership | **Enforced in the state machine as of REC-10** | Any number of drivers | A holder that dies blocks the step until its claim expires or an operator reclaims it | — | `advance()` logs foreign-held steps; `expire-claim` is audited | `take` after expiry | `tests/test_ownership.py` **[RV]** | — | ~~REC-10~~ done |
| ~~REC-02 leases (old finding)~~ | ~~Granted and honoured by `agent_api` only~~ | Single driver, or all work through the HTTP API | Any in-process `advance()`/`sweep()` alongside an external worker → both do the step | Ownership is a lease *record*, not a precondition of the `complete`/`fail` mutations; the REC-08 fix (drive `in_progress` steps) made the driver claim-blind by design | Only the loser's 409 at submit time, after the spend | Manual; the discarded output is not stored | Probe P5 **[RV]** | No test runs two drivers against one run | **REC-10** |
| WF-05 cycle budget | Enforced exactly | Total mutations < `max_cycles` (measured 2/plain step, 3/checked) | Budget hit mid-run → terminal `blocked_cycle_limit`, completed work stranded, re-drive a silent no-op | `mutate()` charges every event to one global counter; the validator hard-caps it at 100 | `health` → `failed`; `escalation` → `run_failed` notice ✅ | **None** — no budget-extension or resume path | Probe P3 **[RV]** | No test for exhaustion mid-run or for re-drive afterwards | **REC-11** |
| WF-04 idempotent mutations | Works when a caller reuses an id | Human/CLI callers passing a stable `--request-id` | Executor path never reuses an id, so a crash-and-resweep re-dispatches and re-pays | `request_id()` mints a uuid per call | None | Re-dispatch is safe for state, costly in spend | Probe P6 **[RV]** | No test of executor crash and resume | **WF-13** |
| NOTIFY-01 human channel | Queues correctly, dedupes correctly | An operator opens the outbox or console | No push; `deliver()` reports success-shaped output when nothing was configured | Sending is a 🟡 action, so outward delivery is deliberately opt-in | Notices exist and are correct | The human must look | Probe 2 **[RV]** | No test asserts delivered ≠ queued | **NOTIFY-02** (plus NOTIFY-01 delivery) |
| HITL-01 authority gates | Enforced on the autonomous path | — | None found | — | Status + notice | — | Probe 2 **[RV]** | — | None |
| OBS-04 health / escalation | Correct classification and dedupe | — | None found | — | Self-reporting | — | Probe 2 **[RV]** | — | None |
| VCS-01 version control | Absent | — | A first commit preserves code but no run evidence | `.gitignore` excludes runs, evidence and workflow snapshots | — | — | `.gitignore` **[V-static]** | — | **VCS-02** |
| AGENT-06 role tool scope | No declaration surface | — | EXEC-01 cannot be scoped | Agent files carry no `tools:` field (0 of 17) | — | — | **[RV]** | — | AGENT-06 first |

## 13. External research applied

**Fencing tokens for lease safety (REC-10).** Kleppmann, *How to do distributed locking*
(2016): a lease alone is not sufficient, because the holder can pause or be delayed past
expiry and still issue a write. The fix is a monotonically increasing **fencing token**
carried with every write, which *the storage service actively checks*, rejecting any write
whose token has gone backwards.
<https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html>

- *Problem it solves:* exactly probe P5 — two actors believing they own one step.
- *Applicability:* direct, and cheap here. The runtime already has a monotonic value per
  run (`seq`) and already refuses stale work via `workflow_revision` on `complete`. The
  missing piece is that ownership is not a precondition of the mutation.
- *Compared with the current implementation:* today the lease is checked by the *caller*
  (`agent_api`) — the "trusted client" pattern the article shows to be unsafe — and the
  in-process driver does not check at all.
- *Mechanism to extract:* mint a token on claim, store it in the step's state, and make
  `complete`/`fail` reject any token that is not the current one. Both drivers then obey
  one rule, and the loser fails fast, before spending.
- *Not adopted:* the article's wider argument about consensus systems (ZooKeeper/etcd) —
  irrelevant to a single-host, file-locked runtime. **[Established practice; the mapping
  onto this codebase is our inference.]**

**Fail-closed validators (VAL-07).** Standard "fail securely" practice: a control that
cannot execute must not be recorded as passed. Applied here, a grader outage should park
the step for a human — or retry with backoff, then park — never mark it graded. The
runtime already has the right state for this (`awaiting_approval`), so no new concept is
needed. **[Established practice; the state mapping is our inference.]**

**Retryable vs. terminal execution limits (REC-11).** Durable-workflow engines (Temporal,
AWS Step Functions) treat an execution-budget breach as an *alarmable, resumable*
condition with an explicit continue path, not a silent terminal state. Applied here,
`blocked_cycle_limit` should be resumable by a human-approved budget extension, and
re-driving a terminal run should report that it is terminal rather than returning quietly.
**[Established practice; the applicability judgement is ours.]**

## 14. Tracker changes from this cycle

**New items**

| ID | Deliverable | Status | Why | Pri | Evidence |
|---|---|---|---|---|---|
| VAL-07 | Quality gates fail closed | ✅ | `acceptance_failure` retries a stalled grader 3× with backoff, then raises. Driver and agent API both keep the deliverable, park the step at `awaiting_approval` with the reason recorded, and write the audit line. Approving finishes the step with the work already produced — the agent is never asked for it twice. The console states why it is parked | ~~P0~~ done | 11 tests, `tests/test_grading.py` + `module-grading.sh` |
| REC-10 | Step ownership enforced by a fencing token | ✅ | A claim mints `holder` + monotonic `claim_token` + `claim_expires_at` in the run state. `complete`/`fail`/`hold` refuse a token that is not the current one; `advance()` skips steps another holder is working; `take` picks up unheld work; `expire-claim` is an audited operator reclaim. Approve releases the claim so the next driver takes it | ~~P0~~ done | 10 tests; probe P5 re-run: driver dispatched 0 times (was 1), worker's submit accepted (was rejected) |
| REC-11 | Cycle-budget exhaustion is resumable and loud | ⛔ | Terminal, strands work, re-drive is a silent no-op | P1 | Probe P3 |
| WF-13 | Executor mutations are replay-safe | ⛔ | A uuid per call makes WF-04 unreachable | P1 | Probe P6 |
| NOTIFY-02 | `deliver()` distinguishes sent from queued | ⛔ | Same return for "sent 3" and "sent nothing" | P2 | Probe 2 |
| VCS-02 | Decide what run evidence is versioned | ⛔ | `.gitignore` excludes every gold-run artifact | P1 | `.gitignore` |

**Amended items**

- **REC-02** 🟡 (was ✅) — leases exist and work for `agent_api`, but are not a precondition
  of any mutation; superseded in part by REC-10.
- **VAL-02** 🟡 (was ✅) — the gate is real but fails open; superseded in part by VAL-07.
- **WF-04** 🟡 (was ✅) — the protection is real but unreachable on the autonomous path.
- **OBS-04 and the detection half of NOTIFY-01** ✅ — **confirmed by runtime probe**, not
  only by tests.
- **HITL-01** ✅ — **confirmed by runtime probe** on the autonomous path.
- **AGENT-06** — promoted to a hard prerequisite of EXEC-01 (0 of 17 agent files carry a
  `tools:` field, so there is nothing to scope with).
- **SKILL-04** — blocked on VCS-01: without history, an intentional prose rewrite cannot be
  distinguished from an accidental one.

**Revised P0 sequencing (supersedes §6 for ordering)**

```
VCS-01 git init ──► SKILL-04 + DOC-07 (suite green)
   │
   ├──► AUD-01/02 audit writer          (unchanged: still the first build)
   │
   ├──► VAL-07 fail-closed grading      (new P0, small, no new concepts)
   ├──► REC-10 fencing token on steps   (new P0, blocks safe multi-driver operation)
   │
   └──► AGENT-06 tool declarations ──► EXEC-01 real tool access
                 │
        DEP-07 scheduler service ──► HOOK-02/03 triggers
```

VAL-07 and REC-10 are both **prerequisites of running the scheduler as a service**
(DEP-07): the moment two drivers can run unattended, probe P5 becomes routine rather than
hypothetical, and a fail-open grader stops being observable by a human at the keyboard.

## 15. Next recommended action (smallest set that removes the most uncertainty)

**`git init` + first commit (VCS-01/VCS-02), then the audit writer (AUD-01/AUD-02).**
Unchanged from REV2 §8, and now better justified: VCS-01 is a hard prerequisite of
SKILL-04 (I-09), and the two new P0s found this cycle — VAL-07 and REC-10 — both change
governance-critical code paths that must not be edited before there is a way to review and
revert them.

Do **not** start VAL-07, REC-10, AGENT-06 or DEP-07 in the same session. VAL-07 and REC-10
are the next two, in that order, once history exists.
