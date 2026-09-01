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

> **Status note, cycle 2 (2026-09-01, later).** Items 1–5 below were the position when
> this section was written. All five have since been closed and verified; the current
> position is §16. They are left in place because the reasoning is still the clearest
> statement of what was wrong, and striking them out would hide how the product moved.

**How close it is.** The **execution spine is real and tested**. What is still missing is
everything that connects that spine to the world:

1. ~~**Agents cannot act — only write.**~~ **CLOSED** (AGENT-06/EXEC-01). `runtime/backends.py:56`
   dispatched every step as `claude -p … --allowedTools ""`. Tools are now granted per role
   and scoped to a per-step workspace; a department produces real files, hashed into the
   evidence.
2. ~~**Nothing can start work except a person.**~~ **CLOSED** (HOOK-02/03, TOOL-07, DEP-07).
   `POST /v1/webhooks/{org}/{connector}` verifies an HMAC signature and enqueues a
   pre-registered goal; a schedule store fires on the clock; the scheduler runs
   `--supervised` under a systemd unit or a Windows scheduled task and treats an idle pass
   as normal rather than as a reason to exit.
3. ~~**Governance evidence is still self-reported.**~~ **CLOSED** (AUD-01/02). `runtime/audit.py`
   writes the line as a side effect of the gate; live connector calls now write one too.
4. ~~**The read-model half has never been instantiated here.**~~ **CLOSED** (ARCH-06). The store
   exists, four gold runs are projected, and a live run was decided over HTTP.
   (`state/` and `scripts/org_state.py` remain unused — WF-10/MEM-04, still open.)
5. ~~**The suite is red, and the tracker says it is green.**~~ **CLOSED** (TEST-01, SKILL-04,
   DOC-07). `bash tests/run.sh` → `SUITE: PASS`, exit 0, verified twice this session.

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
| Agent tool access | Let departments touch real systems | Per-role grants from `tools.json`, scoped to a per-step workspace; Bash/WebFetch/Task ungrantable | ✅ | `runtime/tools.json`, `tests/test_tools.py`; live CFO run produced a real CSV | Connectors and network stay ungranted to agents — those go through the gateway | — | Departments do work, not prose |
| Maker-checker driving | Independent review without a human | `drive_check()`; unreadable verdict treated as RETURN | ✅ | `runtime/checking.py`; 7 tests; live `gold-mc-03` | — | — | Quality loop closed |
| Output validation | Reject stubs/refusals; grade vs. criteria | Structural gate + opt-in acceptance grader | ✅ | 6 gate tests; live `gold-graded-01` rejected then passed | Grader is same model family as author | — | Prevents junk advancing |
| Planner | Goal → validated workflow DAG | `runtime/planner.py`, 3 repair attempts against real validator | ✅ | 9 tests; live `fix-onboarding` = 18 steps / 10 depts | Plans are structural, not data-informed | Tool access | Goal intake works |
| Scheduler | Drive runs without a human command | `sweep()` now takes in new work before driving; `serve(stop_when_idle=False)` with a signal-driven clean stop | ✅ | `runtime/scheduler.py`; 56 scheduling tests incl. supervised-loop and triggered-sweep | Single replica; no leader election if two hosts run it | OPS-01 | Autonomy survives the shell closing |
| Triggers (webhook / cron / SLA) | Let the world start work | Signed `/v1/webhooks/{org}/{connector}`, a schedule store fenced on `next_fire_at`, replay-safe intake queue | ✅ webhook + cron · ⛔ SLA | `runtime/triggers.py`; 50 trigger tests; live end-to-end run | SLA/threshold clocks | HOOK-04 | The system notices, not only the human |
| Human notification | Tell a person a gate is waiting | `runtime/notify.py` outbox + optional operator-wired command | ✅ | `runtime/runs/_outbox.jsonl` (1,428 B, populated) | Delivery command not configured; no SLA/age escalation | — | Silence problem solved locally |
| Approval console | Decide from a brief, not a wall of prose | `approval_server.py` + `briefing.py`, 5-line ASK/IF YES/FINDINGS/WATCH/RECOMMEND, ordered by blast radius | ✅ | 20 tests incl. ordering + XSS; `*.release-output.brief` files | Local, single operator, no auth | — | Human latency reduced |
| Approval UI (Control Center) | Approve/reject from the web app | Queue screen reads `GET /v1/decisions` and posts a decision with a required reason; red steps render as handed back | ✅ | 29 tests incl. real HTTP; `module-decisions.sh` | No run-detail/timeline view; no trigger or in-flight-receipt screen | UI-02 | Gates answerable from the web |
| Agent-facing API | Out-of-process agents claim/submit work | `GET /v1/work`, `/v1/claim`, `/v1/submit`, `/v1/heartbeat`, `/v1/fail` | ✅ | `runtime/agent_api.py`; 22 tests | One shared bearer token; no per-agent identity | — | Enables external workers |
| Leases / liveness | Detect a hung step | `runtime/leases.py` grant/renew/release/expire | ✅ | 149 lines + tests | No lease file present (never exercised outside tests) | — | Recovery path exists |
| Run health / stall detection | Know what is stuck | `runtime/health.py`: running / waiting / stalled / finished / failed | ✅ | 7 health tests | No alerting — a person must look | OBS-02/03 | Self-monitoring, unnotified |
| Escalation | Raise what needs a person | `runtime/escalation.py` scan → notices | ✅ | 112 lines + module suite | Notices generated but drive no run (observed in prior session) | notify delivery | Partial |
| Memory (cross-run) | Don't re-solve the same problem | Append-only, hash-chained, propose→approve→recall into prompts | ✅ | 17 tests; live lesson crossed runs | Keyword recall only; 2 entries total | — | Working, barely used |
| Org state (goals/tasks/decisions) | Durable accountability outside runs | `scripts/org_state.py` written | ⛔ | `state/` = `README.md` only | Any adoption at all | — | Dead code today |
| State architecture | One answer to "what is ready?" | Log = execution record; SQLite = identity + read model; one-way `projection.py` | ✅ | 15 projection tests; migration `004` | `runtime/data/` absent here → store never created; Control Center still reads the old shape | — | Drift resolved by decision |
| Audit log writer | Tamper-evident "who approved what" | `runtime/audit.py` — validated, hash-chained, fsynced, locked; written by the gate and by every live connector call | ✅ | 14 audit tests + 21 live-gateway tests; chain verifies | SLA and escalation events still unwired | — | Governance is recorded, not claimed |
| Connectors | Reach external systems | Real HTTPS adapter: resolve-time SSRF check, env-only secrets, intent recorded before the send, three-outcome settlement | ✅ adapter · 🚧 a live provider | `runtime/live_gateway.py`; `tests/test_live_gateway.py` 21/21 | No provider authorized yet — a human OAuth act, by design | Human OAuth | The hands exist; nobody has shaken one |
| MCP / plugin config | Bind declared skills to real tools | **None in repo** | ⛔ | no `.mcp.json`, no `plugin.json` | Tool bindings for the 124 declared skills | ARCH-05 | Declared ≠ usable |
| HTTP service | Operator/API boundary | 16 routes, HMAC short-lived tokens, DB-bound roles, rate limit, headers, body cap | ✅ | `runtime/api.py`, `auth.py`, `db.py` | No run/step read routes; no OpenAPI | ARCH-01 | Sound |
| Test suite | Prove behaviour | 14 shell modules + ~43 Python test modules | ✅ | `bash tests/run.sh` → `SUITE: PASS`, exit 0 (run twice this session) | Still ~majority grep-over-Markdown; the behaviour share is rising | TEST-02 | Verification integrity restored |
| CI | Catch regressions | compileall, suite, CodeQL, SBOM, npm audit, release gate | 🟡 | `.github/workflows/ci.yml`; 11 commits on `main` | ubuntu-only matrix; never executed on a remote — no push target is configured | TEST-05, VCS-03 | The local suite is the only gate today |
| Deployment | Run it for real | systemd api/backup/maintenance **and scheduler** units, a Windows scheduled-task installer, reverse proxy, env template, worker | 🟡 | `deploy/myorg-scheduler.service`, `deploy/install-scheduler-windows.ps1` | Never deployed to a real host; no rollback drill; neither unit has been executed by its supervisor | PROD-04/05 | Unattended operation is installable, not yet installed |
| Python dependency manifest | Reproducible toolchain | `pyproject.toml`: Python ≥3.11, zero dependencies, enforced by a test that walks every import in the tree | ✅ | `tests/test_dependencies.py` 4/4 | — | — | No supply chain to compromise |
| State architecture isolation | A test can never write to the company's data | The read model follows `MYORG_RUNS_DIR`, as the run log and the audit log already did | ✅ | `test_a_sweep_never_mirrors_into_the_companys_real_database` | — | — | Fixed after ten fabricated runs were found in production |

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
| ARCH-06 **(new)** | Store instantiated on a real host | ✅ | `admin bootstrap` creates store + org + first operator + token in one idempotent command and prints the next steps; refuses without `MYORG_AUTH_SECRET`. Run for real here: 4 gold runs projected, and a live run was parked, decided over HTTP, and agreed across run log, read model and audit log | Single host; no managed identity yet (PROD-02) | — | ~~P1~~ done | 9 tests; `runtime/data/myorg.db` exists, `admin verify` → integrity ok |
| WF-01…07,12 | Schema, DAG, hash chain, idempotency, caps, evidence, terminal states | ✅ | All present | — | — | — | `company_runtime.py` |
| WF-08 | Goal → workflow | ✅ | `planner.py`, validator-checked, 3 repairs | Not data-informed | G1 | P1 | 9 tests; `fix-onboarding` |
| WF-09 | Workflow library | ⛔ | 2 gold-run proofs | Real business workflows | WF-08, G1 | P1 | `runtime/workflows/` |
| WF-10 | Org state adoption | ⛔ | `org_state.py` written | Any use at all | — | P1 | `state/` = README |
| WF-11 | Cross-run prioritization | ⛔ | Nothing | Queue ordering | ARCH-06 | P2 | — |
| AGENT-05 | Executor | ✅ | Claim → dispatch → grade → evidence → complete | Tools disabled | G1 | — | 9 tests; `gold-auto-01/02` |
| AGENT-08 | Maker-checker driving | ✅ | `drive_check()` | — | — | — | `gold-mc-03` |
| AGENT-09 | Handoff payload | ✅ | Hash-verified upstream evidence | Direct edges only (deliberate) | — | — | 5 handoff tests |
| LOOP-02 | Execution loop | ✅ | `advance()` bounded | — | — | — | `test_the_driver_never_loops_forever` |
| LOOP-03 | Scheduled sweep | ✅ | `sweep()` takes in triggered work and then drives; `serve()` distinguishes a *command* (stop when idle) from a *service* (idle is normal) | Single replica only | — | ~~P0~~ done | 56 scheduling tests; `deploy/myorg-scheduler.service` |
| LOOP-04 | Feedback loop | ✅ | Checker reasons → next attempt | Run-level feedback | — | P2 | `gold-mc-02` |
| LOOP-05 | Learning loop | 🟡 | Auto-proposes a lesson on RETURN/REJECT | Signal capture beyond checker verdicts | MEM-06 | P2 | `memory.py` |
| LOOP-06 | Checkpoint / budget loop | ⛔ | Documented caps only | Time/cost budget enforcement | — | P2 | `operating-model.md` §4 |

### 5.2 Reaching the world

| ID | Deliverable | Status | What Exists | What Is Missing | Deps / Blockers | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| EXEC-01 **(new)** | Governed tool access for dispatched agents | ✅ | Each step runs in its own workspace (`runtime/workspaces/<run>/<step>/`) with `--tools`, workspace-scoped `--allowedTools` and `--permission-mode dontAsk`. Files the agent leaves are hashed into the evidence, so the recorded hash covers the artifacts as well as the text | Local files only; connectors and network still ungranted (TOOL-03/04) | — | ~~P0~~ done | live run: `cfo-finance` produced a real CSV + model; 16 tests |
| AGENT-06 | Per-role tool permissions | ✅ | `runtime/tools.json`, the same shape as `policy.json`: a default grant plus per-role overrides, validated on load. Bash, WebFetch, WebSearch, Task and Agent are ungrantable with the reason recorded — Bash is scoped by command, never by path, so no workspace can bound it | Every role currently takes the default; overrides exist but none are needed yet | — | ~~P0~~ done | `tests/test_tools.py` |
| HOOK-01 | Claude Code hooks | ⛔ | None | PreToolUse/PostToolUse enforcement of §3 | EXEC-01 | P1 | `.claude/` has no hooks |
| HOOK-02 | Inbound webhook trigger | ✅ | `POST /v1/webhooks/{org}/{connector}`: HMAC + nonce + skew window, no bearer token, rate-limited per connector, and one identical refusal for every rejection so the route cannot be used to enumerate what we listen for. The payload selects a **pre-registered** goal and never supplies one | Only the `event_type` field is read; richer payload→context mapping is future work | — | ~~P0~~ done | 24 trigger tests incl. real HTTP; live e2e |
| HOOK-03 | Cron / calendar triggers | ✅ | `schedules` table with `next_fire_at` as a fence — claiming and advancing are one UPDATE, so two sweepers fire it once. A schedule that fell behind catches up once, not for every interval it missed | UTC only; no calendar (RRULE) semantics | — | ~~P0~~ done | 9 schedule tests incl. a two-thread race |
| HOOK-04 | SLA / threshold triggers | ⛔ | SLA described in `lead-response` | Clock + breach event, on top of the schedule store now in place | HOOK-03 | P1 | — |
| TOOL-03 | Real execution gateway | ✅ | `runtime/live_gateway.py`: resolve-time address check (a name public at admission can point inside by call time), secrets read from the environment only, no redirects, response ceiling, and a receipt written **before** the send | Only bearer-token auth; no OAuth refresh flow yet | — | ~~P1~~ done | `tests/test_live_gateway.py` 21/21 |
| TOOL-04 | Live connectors | 🚧 | The adapter is real and tested against a fake provider; no real provider is authorized | A human OAuth grant against an actual vendor, then provider-specific tests | TOOL-03, human | P1 | ledger 0.1; `authorize_connector` path exercised |
| TOOL-07 | Webhook ingestion route | ✅ | Folded into HOOK-02 — same route, same tests | — | — | ~~P1~~ done | `WebhookOverHttpTest` |
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
| VCS-01 **(new)** | Version control | ✅ | Git repository on `main`, 11 commits, conventional messages, `.gitattributes` and `.gitignore` in place | No remote configured, so nothing is pushed or backed up off this machine | VCS-03 | ~~P0~~ done | `git log` |
| TEST-01 | Suite green | ✅ | 14 shell modules + ~43 Python modules | — | — | ~~P0~~ done | `bash tests/run.sh` → `SUITE: PASS`, exit 0 |
| SKILL-04 **(new ID for the 3 prose failures)** | Governance text matches tests | ✅ | Per-send gating wording restored in `demand-gen` and `privacy-program` | — | — | ~~P1~~ done | revenue-engine 40/0, trust-compliance 36/0 |
| TEST-02 | Behaviour vs. prose ratio | 🟡 | Real behaviour tests now exist for executor/planner/scheduler/memory/health | Majority of checks still grep Markdown | — | P1 | suite inspection |
| TEST-05 | Cross-platform CI | ⛔ | ubuntu-24.04 only, never executed | windows-latest matrix | VCS-01 | P1 | `ci.yml` |
| DEP-06 | Python dependency manifest | ✅ | `pyproject.toml`: Python ≥3.11, **zero** dependencies — the runtime is stdlib-only, which is why the state machine, lock, HTTP boundary and gateway are hand-written | — | — | ~~P1~~ done | `tests/test_dependencies.py` walks every import and names any offender |
| DEP-07 **(new)** | Scheduler/executor service unit | ✅ | `deploy/myorg-scheduler.service` (hardened, `Restart=on-failure`, SIGTERM finishes the pass in flight) and `deploy/install-scheduler-windows.ps1` for the platform this host actually runs | Neither has been executed by its supervisor on a real host | PROD-04 | ~~P0~~ done | 4 supervised-loop tests |
| DOC-07 | `CLAUDE.md` within guardrail | ✅ | Inside the size cap | — | — | ~~P1~~ done | CORE 51/0 |
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
| ~~0~~ | ~~Agents cannot use tools~~ | AGENT-06, EXEC-01 | **CLOSED 2026-09-01.** A department can now produce real artifacts, inside a boundary that was measured rather than assumed | Real deliverables; data-informed planning; the connector work that follows |
| ~~0~~ | ~~Two drivers can do the same step~~ | REC-10 | **CLOSED 2026-09-01.** Ownership is a precondition of the write, not a courtesy | Safe multi-replica workers; unblocks DEP-07 and the Docker migration |
| ~~0~~ | ~~Quality gates fail open~~ | VAL-07 | **CLOSED 2026-09-01.** A control that cannot run no longer reports a pass | Trustworthy unattended grading; safe multi-replica operation later |
| ~~1~~ | ~~Governance evidence is self-reported~~ | AUD-01, AUD-02 | **CLOSED 2026-09-01.** `runtime/audit.py` writes the line as a side effect of the gate; the transition fails closed if the log cannot be written. RCA-A's root cause — control and evidence on the same side of the trust boundary — is removed for gate transitions | Trustworthy unattended operation; HITL-05; every governance claim in the ledger |
| ~~2~~ | ~~Agents cannot use tools~~ | EXEC-01, AGENT-06 | **CLOSED 2026-09-01.** Scoped grants inside a per-step workspace | Real deliverables; the connector work that followed |
| ~~3~~ | ~~Nothing can start work but a person~~ | HOOK-02, HOOK-03, TOOL-07 | **CLOSED 2026-09-01 (cycle 2).** A signed webhook or the clock starts a run; the payload selects a pre-registered goal and never supplies one | Revenue-engine and compliance skills whose value is timeliness. HOOK-04 (SLA clocks) remains |
| ~~4~~ | ~~The loop is not a supervised service~~ | LOOP-03, DEP-07 | **CLOSED 2026-09-01 (cycle 2).** An idle pass is the normal state of a service; SIGTERM finishes the pass in flight; a second loop is refused | Unattended operation; stall alerting |
| ~~5~~ | ~~No version control~~ | VCS-01 | **CLOSED.** 11 commits on `main` | CI — though no remote is configured yet (VCS-03) |
| ~~6~~ | ~~Suite is red while the tracker says green~~ | TEST-01, SKILL-04, DOC-07 | **CLOSED.** `SUITE: PASS`, exit 0 | Trustworthy "done" |
| ~~7~~ | ~~Nothing observes the autonomous half~~ | OBS-08 | **CLOSED 2026-09-01 (cycle 2).** Runs, approval age, queue depth and unresolved outward calls are exported and alerted — and the collector reports its own blindness | Every guarantee above is now one somebody would notice failing |
| 8 | Standing autonomy is `curl`-only | UI-02 | Triggers and schedules can be created and paused only through the API. For a product whose safety story is human oversight, the newest and least reversible controls are missing from the oversight surface. | Operator confidence; safe day-to-day use |

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

> **Superseded by §20.** Everything named below was completed. The current recommendation is
> OBS-08 — instrument the autonomous half.

~~**`git init` + first commit (VCS-01/VCS-02), then the audit writer (AUD-01/AUD-02).**~~
Unchanged from REV2 §8, and now better justified: VCS-01 is a hard prerequisite of
SKILL-04 (I-09), and the two new P0s found this cycle — VAL-07 and REC-10 — both change
governance-critical code paths that must not be edited before there is a way to review and
revert them.

~~Do **not** start VAL-07, REC-10, AGENT-06 or DEP-07 in the same session.~~ All four have
since landed, each with its own tests.

---

# Investigation Cycle 2 — connectors, triggers, and the loop as a service (2026-09-01, evening)

Scope as agreed: **TOOL-03/04** (real connectors) and **DEP-07 + HOOK-02/03** (run the loop
as a service, with triggers). Plus a tracker truth pass, and an independent
production-readiness review that was deliberately *not* bounded by this tracker.

Method: read the code, then run it, and prefer evidence produced by execution over evidence
produced by reading. Tags as before: **[V]** verified by execution here · **[V-static]**
verified by reading · **[I]** inference · **[?]** unknown.

## 16. Where the product actually stands

**Verified baseline, before any change [V].** `bash tests/run.sh` → `SUITE: PASS`, exit 0.
The tracker had carried `TEST-01`, `SKILL-04`, `DOC-07` and `VCS-01` as open since REV2 was
written; all four had in fact been closed by the commits that landed after it. That is the
opposite of the REV1→REV2 drift — documentation behind the code rather than ahead of it —
but it is the same failure, and it wants the same corrective: status belongs to a gate, not
to prose.

**Verified after this cycle [V].** `bash tests/run.sh` → `SUITE: PASS`, exit 0, with 94 new
checks. The autonomous path was then exercised end to end against a real store, a real HTTP
server, a real HMAC signature and the real state machine:

```
[ 6] signed webhook accepted (202)  -- tg-ca82ef81a4160d674f360955
[ 7] forged webhook refused (403)   -- signature checked before anything is queued
[ 9] injection boundary held        -- the payload's `goal` field never reached an agent
[11] scheduler swept                -- started 1, drove 1, 0 idle
[12] a run exists that no human created -- run-ca82ef81a4160d674f360955
[13] created by                     -- trigger:webhook / request trigger-tg-ca82...
[15] run status                     -- active; health says waiting on you
[18] audit chain                    -- intact
[19] operator read model            -- visible: True, status active
```

The run planned itself, drove `frame-goal` and `produce-output`, stopped at
`release-output` because that step is yellow, wrote its audit line, raised a notice and
appeared in the operator read model. **That is the north star of `CLAUDE.md` §1 executing
with no person in the loop, and halting at exactly the point the constitution says it
must.**

## 17. What was built this cycle

| ID | What | Evidence |
|---|---|---|
| TOOL-03 | `runtime/live_gateway.py` — a real HTTPS adapter behind the existing control plane | `tests/test_live_gateway.py` 21/21 |
| HOOK-02 / TOOL-07 | `POST /v1/webhooks/{org}/{connector}` — the one route with no bearer token | 24 trigger tests incl. real HTTP |
| HOOK-03 | `schedules` table with `next_fire_at` as a fence; a replay-safe `trigger_intake` queue | 9 schedule tests incl. a two-thread race |
| DEP-07 / LOOP-03 | `serve(stop_when_idle=False)`, signal-driven clean stop, single-instance guard, systemd unit and Windows installer | 7 supervised-service tests |
| DEP-06 | `pyproject.toml` — Python ≥3.11, zero dependencies, enforced by a test that walks every import | `tests/test_dependencies.py` 4/4 |

**The design decision that matters most.** A live call has three outcomes, not two: it
worked, it failed, or *the bytes left and we never heard back*. A fixture can pretend there
are only two. A real provider cannot. So the gateway consumes the approval and writes an
`in_flight` receipt **before** a single byte leaves, settles it afterwards, and classifies
honestly — 2xx `accepted`, 4xx `failed`, and **5xx, timeout or no-response `in_flight`**. A
retry against an unresolved receipt is refused rather than re-sent:

```
test_retrying_an_unresolved_call_refuses_instead_of_sending_again
    self.assertEqual(second.sends, [], "an unresolved call must not be sent a second time")
```

Recording an unknown outcome as "failed" is exactly what makes a retry charge a customer
twice. The runtime now says "I do not know", and hands it to
`GET /v1/connectors/in-flight` where a person reconciles it.

**TOOL-04 stays 🚧, correctly.** The adapter is real and tested against a fake provider, but
no live provider is authorized — and authorizing one is a human OAuth act under §3, not
something a session can do on the user's behalf.

### 17.1 What the real-model run revealed [V]

The end-to-end proof was run twice: once with stub backends, and once with real `claude -p`
for both planning and execution. The real run is worth reading closely, because it found the
product's honest limit without anyone looking for it.

The planner turned the triggered goal into a `revops` step (`pull-inbound-lead-data`) and a
downstream analysis step. RevOps ran, and reported — correctly — that it had no data, because
no CRM connector is authorized. The acceptance grader then **rejected** the analysis:

```
analyze-volume-trend: rejected -- did not meet acceptance criteria: VERDICT: FAILS
  Criterion 1 (week-over-week change as number and %) -- missed. The note explicitly
  refuses to state any figure.
  Criterion 3 (figures trace to prior step's data) -- missed vacuously. There are no
  figures to trace, and the prior step delivered no data.
```

It retried twice more. The second attempt tried to fill the gap with numbers derived from a
`√N` formula rather than from the pull, and the grader caught that too — *"the only numbers
present (39%, 28%, 20%…) come from a √N formula, not from the previous step's data"*. After
three attempts the run terminated `blocked_retry_limit`, health `failed`, visible in the
operator read model as `blocked`, audit chain intact.

Four things are established by that, and none of them by a test:

1. **The agent did not fabricate.** Asked for lead volume with no source of lead volume, it
   said so rather than inventing plausible numbers — the failure mode that would make this
   whole product dangerous.
2. **The quality gate caught it anyway — including the one attempt that did drift.** VAL-02
   rejected an empty-but-honest deliverable *and* a filled-in-with-arithmetic one, on live
   output rather than on a fixture. Belt and braces both held.
3. **It failed loudly rather than shipping.** `blocked_retry_limit`, `health = failed`, a
   notice raised, `blocked` in the operator view. The worst outcome for a product like this
   is a plausible answer nobody asked where it came from; what happened instead was a
   visible stop.
4. **TOOL-04 is the binding constraint on usefulness, and only on usefulness.** The
   execution spine, the triggers, the gates, the grading and the audit trail all work. What
   the company lacks is not machinery — it is a connected system to reason about. Every
   department capability stays a claim until one real provider is authorized.

This is the most useful single piece of evidence in this cycle: it separates "the runtime
does not work" (false) from "the runtime has nothing to work on" (true, and fixable only by
a human granting access).

## 18. Independent production-readiness review

Not bounded by this tracker. Each finding: what · why it matters · evidence · impact · fix ·
priority · how to verify.

### 18.1 Verified issues (reproduced here)

**TEST-07 — the test suite was writing into the company's production database. FIXED.**
*Evidence [V]:* reading `runtime/data/myorg.db` found **ten fabricated runs** — `sch-a`,
`sch-b`, `sch-cap`, `sch-done`, `sch-good`, `sch-hold`, `sch-loop`, `sch-nodb`,
`sch-signal`, `sch-stall` — every one a fixture from `tests/test_scheduling.py`.
*Cause [V-static]:* `MYORG_RUNS_DIR` redirected the run log and, since AUD-01, the audit
log — but **not** the projection target, so `scheduler.mirror()` faithfully copied test runs
into the real read model.
*Impact:* the operator view of the company was two-thirds fiction; any metric, queue depth
or capacity figure derived from it was wrong; and a test could corrupt production state.
*Fix:* `projection.default_db()` now follows `MYORG_RUNS_DIR`, the same rule
`audit.log_path()` already used. The ten rows were removed after backing up the database.
*Verify:* `test_a_sweep_never_mirrors_into_the_companys_real_database`.
**Priority: P0 — done.**

**HITL-06 — the audit log asserted something nobody had checked. PARTLY FIXED.**
*Evidence [V-static]:* `company_runtime.approve` takes `--approver` as a free string that
nothing authenticates, and the audit note read `approved by a named human`.
*Why it matters:* the audit log exists precisely so governance claims are not self-reported.
A record saying "a named human" when no human was verified is the same class of defect
AUD-01 was built to remove, one layer up.
*Fix:* the note now states what was actually checked — `a registered active human`, or
`not a registered actor in this organization`, or `name self-asserted at the CLI,
unverified`. *Verify:* three new tests in `tests/test_audit.py`.
*Remaining:* the CLI still cannot authenticate. Binding it to the store, or retiring the CLI
approval path in favour of `/v1/decisions`, is the real fix. **P1, open.**

**SEC-08 — a valid signature was an unbounded bill. FIXED.**
*Evidence [V-static]:* every queued trigger becomes a *planned* run, planning is a model
call, and the intake queue had no ceiling. A provider retrying in a loop — or a leaked
signing key — would have spent without limit, unattended.
*Fix:* `MAX_QUEUED_TRIGGERS = 50`; a full queue is a visible refusal naming the backlog
rather than silent spend. *Verify:* 4 tests in `BackpressureTest`. **P1 — done.**

**OPS-01 — two supervised loops could both drive the company. FIXED.**
*Evidence [V-static]:* nothing stopped a second `python -m runtime.scheduler --supervised`.
Steps are fenced (REC-10) and schedules are fenced by `next_fire_at`, so state could not
corrupt — but both loops would plan the same goals and pay for the same steps. The ordinary
way to get two is a unit restarting while an operator has one open in a terminal.
*Fix:* a file lock on the runs directory. `--once` is deliberately exempt, so the company
stays inspectable while it works. *Verify:* 3 tests. **P1 — done.**

**DEBT-02 — three modules are past the house limit. OPEN.**
*Evidence [V]:* `db.py` 933, `company_runtime.py` 553, `api.py` 508, `service.py` 415
against a 300-line rule; this cycle made two of them worse. *Impact:* review quality and
change risk, not correctness. *Fix:* split `db.py` by aggregate (identity · runs ·
connectors · triggers) and lift the `api.py` route chain into a table-driven dispatch.
**P2.**

### 18.2 Probable risks (reasoned from evidence, not reproduced)

**OBS-08 (new) — nothing observed the autonomous half. FIXED.**
`observability.py` exported four HTTP series and `prometheus-alerts.yml` held three rules,
all HTTP [V-static]. There was no metric for runs, approval age, trigger queue depth, or
in-flight receipts. The company had just been given the ability to run unattended, and the
only instrumented component was the one part of it that is *not* autonomous.
*Impact:* a stalled queue, an unanswered approval, or an outward call that left and never
came back were invisible until a person happened to look.
*Fix:* `RuntimeGauges` in `runtime/observability.py`, served on the same token-protected
`/metrics` scrape. Details that matter more than the series list:
  - **Every series is always exported, including at zero.** A gauge that disappears when
    it is zero cannot be alerted on.
  - **Ages report the oldest, never the newest** — reporting the newest would hide exactly
    the decision that has been ignored.
  - **A failing collector is itself a metric.** `myorg_runtime_snapshot_ok 0` is alertable,
    because a collector that failed quietly would recreate this very gap: every alert would
    go silent for the same reason a healthy company does.
  - **One source failing costs its own numbers, not the endpoint.**
  - **Cached for one scrape interval.** Collecting reads every run log; a metrics endpoint
    that slows down as the company gets busier is one people switch off.
  - **No label carries a run id, a goal, an agent or an org.** Labels leak into dashboards
    and alert emails; a test asserts the scrape contains none of them.
*Verify:* `tests/test_runtime_metrics.py` — 36 checks, each creating the real condition and
asserting the number moved. Five alert rules in `deploy/prometheus-alerts.yml`, with a test
that every rule reads a series the runtime actually exports and names a runbook that exists.
**P1 — done.**

**UI-02 (new) — standing autonomy can only be created or stopped with `curl`.**
Triggers, schedules, in-flight receipts and connector authorization are API-only; the
Control Center shows the decision queue and nothing else [V-static].
*Impact:* the operator cannot see what is allowed to wake the company up, or pause it, from
the surface they actually use. For a product whose safety story is human oversight, the
oversight surface is missing the newest and least reversible controls.
*Fix:* a Triggers screen (list, pause, resume) and an Exceptions screen (in-flight receipts,
failed triggers). **P1. Depends on: API-02.**

**TOOL-09 (new) — connector authorizations expire and nothing renews them.**
`authorize_connector` stores `expires_at` up to 366 days out and `_admit` refuses once it
passes [V-static]. There is no refresh flow and no warning.
*Impact:* a connector stops working at a moment nobody chose, mid-run, and the failure looks
like a bug. *Fix:* warn at T-14 days through the notice outbox; implement OAuth refresh when
a real provider is authorized. **P1. Depends on: TOOL-04.**

**VCS-03 (new) — no git remote, so CI has never executed.**
11 commits exist locally; no push target is configured [V].
*Impact:* `.github/workflows/ci.yml` is aspiration, the suite runs only when a human runs
it, and the only copy of the work is one disk. *Fix:* add a remote, push, enable the
workflow. **P1.**

**SEC-09 (new) — no key-rotation window.**
`TokenService` accepts exactly one secret; the token header carries `kid: local-v1` but no
second key is ever tried [V-static]. The webhook signing secret has the same shape.
*Impact:* rotating `MYORG_AUTH_SECRET` invalidates every live token at once, so rotation —
including rotation *after a suspected leak* — is an outage. *Fix:* accept a previous key for
one token lifetime. **P2.**

**REC-13 (new) — a partially created triggered run was orphaned. FIXED.**
If `create_run` succeeded but the bookkeeping did not, the trigger was re-queued and the next
attempt refused with "run already exists", burning attempts until it was marked failed — and
leaving a run nothing pointed at [I, from reading `triggers.start_queued`]. The run id is
derived from the trigger, so finding one already there is not a collision: it is the previous
attempt's work. *Fix:* adopt it. *Verify:*
`test_a_run_a_previous_attempt_created_is_adopted_not_orphaned`. **P2 — done.**

### 18.3 Unverified concerns (need evidence before acting)

- **Neither deployment unit has been executed by its supervisor [?].** The systemd unit and
  the Windows scheduled task are written and reviewed but unrun. Until one is started by
  `systemctl` or `Start-ScheduledTask`, "runs unattended" is a design claim, not a fact.
  Resolving it is PROD-04.
- **The 124 declared external skills remain unexercised [?]** — unchanged from REV2 §9.
- **Grader and author still share a model family (VAL-06) [?]** — unchanged.
- **Two schedulers across two *hosts* [?].** The new guard is a local file lock. A shared
  filesystem would hold; a second machine with its own disk would not.

## 19. Tracker changes from this cycle

**Closed and verified:** VCS-01 · TEST-01 · SKILL-04 · DOC-07 (already done; the tracker was
behind) · TOOL-03 · TOOL-07 · HOOK-02 · HOOK-03 · DEP-06 · DEP-07 · LOOP-03.

**New items**

| ID | Deliverable | Status | Why | Pri | Evidence |
|---|---|---|---|---|---|
| TEST-07 | A test can never write to production state | ✅ | Ten fabricated runs were found in the company's real read model | ~~P0~~ done | `test_a_sweep_never_mirrors_into_the_companys_real_database` |
| SEC-08 | Trigger intake has backpressure | ✅ | A valid signature was an unbounded model bill | ~~P1~~ done | 4 `BackpressureTest` checks |
| OPS-01 | One supervised loop per runs directory | ✅ | Two loops would plan and pay twice | ~~P1~~ done | 3 `single_instance` checks |
| OBS-08 | Metrics and alerts for the autonomous half | ✅ | Only the web server was watched, and the company now runs unattended | ~~P1~~ done | `tests/test_runtime_metrics.py` 36/36; 5 alert rules |
| UI-02 | Operator surface for triggers, schedules and exceptions | ⛔ | Standing autonomy is `curl`-only | **P1** | Control Center shows decisions only |
| TOOL-09 | Connector authorization renewal and expiry warning | ⛔ | Access dies at a moment nobody chose | P1 | `_admit` refuses expired |
| VCS-03 | A git remote, so CI actually runs | ⛔ | CI has never executed | P1 | no remote configured |
| SEC-09 | Key rotation with an overlap window | ⛔ | Rotation is an outage | P2 | `TokenService` holds one secret |
| REC-13 | A partially created triggered run is adopted, not orphaned | ✅ | Attempts used to burn against a run that already existed, then abandon it | ~~P2~~ done | `test_a_run_a_previous_attempt_created_is_adopted_not_orphaned` |

**Amended**

- **HITL-06** 🟡 — sharpened. The defect is not only that approver identity is
  unauthenticated, but that the audit note *claimed* it had been checked. The claim is
  fixed; the authentication is not.
- **DEBT-02** 🟡 — worse, not better: `db.py` 777 → 933, `api.py` 411 → 508.
- **TOOL-04** 🚧 — unchanged, and correctly so.

## 20. Next highest-priority issue

~~**OBS-08 — instrument the autonomous half.**~~ **DONE, same session** — see §18.2. The
company now reports its own runs, approval ages, queue depth and unresolved outward calls,
and says so when it cannot.

**Next: UI-02 — the operator surface for standing autonomy.**

Triggers and schedules are standing permission to act unattended, and today they can only be
created, listed and paused with `curl`. The Control Center shows the decision queue and
nothing else. For a product whose entire safety story is human oversight, the controls that
are newest, least reversible and hardest to notice going wrong are the ones missing from the
surface an operator actually uses.

Two screens, both reading routes that already exist:
- **Triggers** — schedules and webhook triggers, with pause and resume (`GET /v1/schedules`,
  `PUT /v1/schedules/{id}/status`, `POST /v1/triggers/webhook`).
- **Exceptions** — unresolved outward calls and failed triggers
  (`GET /v1/connectors/in-flight`), each with the reconcile action.

The stop button matters more than the start button. A person who cannot pause a schedule
from the screen in front of them will not be able to stop the company at the moment they
most want to.

After that, in order: **VCS-03** (a remote, so CI has ever run) · **TOOL-09** (authorizations
expire silently) · **HITL-06** (CLI approvals are still unauthenticated) · **TOOL-04**, which
remains the binding constraint on usefulness and needs a human, not a session.
