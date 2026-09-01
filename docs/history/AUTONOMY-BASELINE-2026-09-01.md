# Autonomy Baseline & Project Tracker — 2026-09-01

Evidence-driven review of `C:\AgenticAI\MyOrg` against the intended end state of an
**autonomous organization of multiple roles/agents**. Every claim below is tagged:

- **[V]** Verified from implementation (code read and/or executed)
- **[D]** Documented/intended, not verified as working
- **[I]** My inference

Baseline commands run during this review are recorded in §9.

---

## 1. Executive Assessment

**The product is not an autonomous organization. It is a governance and bookkeeping
scaffold for a human-driven organization, plus a large body of prompt documentation.**

Three findings dominate:

1. **There is no execution engine. [V]** Across all 3,212 lines of Python there is
   **zero** code that invokes a model, spawns an agent, calls a provider, schedules
   anything, or runs a loop. `grep` for `anthropic|openai|subprocess|Popen|cron|schedule|
   while True|poll|heartbeat` over `**/*.py` returns only unrelated matches (an HTTP
   method dispatcher, a secret-scanner regex, a test's `subprocess.run`). The 17
   "agents" are Markdown prompt files consumed by Claude Code's own subagent feature —
   they are not processes this product can start, sequence, or supervise.

2. **The core runtime does not run on the developer's own machine. [V]**
   `runtime/company_runtime.py` and `scripts/org_state.py` both `import fcntl`, a
   POSIX-only module. On this Windows 11 host both fail at import:
   `ModuleNotFoundError: No module named 'fcntl'`. Consequently **30 of the
   acceptance tests fail here** (controlled-runtime 14 fail, maker-checker 9 fail,
   organization-management 3 fail) and the whole suite reports
   `════════ SUITE: FAIL ════════`. The orchestrator, the state manager, and the
   maker-checker gate are all inert locally.

3. **Two disconnected systems both claim to be "the runtime". [V]**
   `runtime/company_runtime.py` is a JSONL-file DAG state machine (steps, owners,
   checkers, retries, evidence hashes). `runtime/api.py` + `service.py` + `db.py` is a
   SQLite HTTP service with its own `runs`, `events`, and `approvals` tables. They
   share the `policy.json` file and nothing else. `Store.create_run()` records run
   *metadata* only — no steps, no DAG, no ready-state. Approving a step in the CLI
   does not exist in the API; approving an action in the API does not unblock a CLI
   step. This is architectural drift, not layering.

**Honest scoring against the stated end state:** responsibilities/decision-authority and
autonomy-boundaries are strong (documented and partly enforced). Skills, tasks, tools,
hooks, loops, memory, handoffs, monitoring, recovery — all range from prompt-only to
absent as *executable* mechanisms.

**Important context, in the project's favour:** `docs/RUNTIME-AUDIT.md` states plainly —
*"Excluded now: model/provider calls, automatic agent dispatch, external connector
execution, scheduler, authenticated actor/approver identity, and production deployment.
The workflow is manual-first."* The project has **not** claimed autonomy. Its designed
end state (human-in-the-loop, propose-and-approve) and your stated end state (autonomous
organization) are different targets. That gap is the single most important thing to
resolve before any code is written — see §8.

---

## 2. Intended Autonomous-Organization Architecture

### 2.1 What the product is intended to do

**[V from `CLAUDE.md`, `README.md`]** MyOrg ("Enterprise — The Company OS") is a
Claude Code workspace that behaves as a whole company. A user states a business goal in
plain language ("handle our Q3 board deck", "a customer is threatening to churn") and
the correct department acts without the user naming a skill or agent. The session's root
agent is the **Chief of Staff**, which classifies, decomposes, dispatches, gates, and
synthesizes.

### 2.2 What "autonomous organization" means *in this implementation*

**[V]** Deliberately narrow. `CLAUDE.md` §3 defines a three-colour authority model:

| Class | Meaning | Enforcement found |
|---|---|---|
| 🟢 Green | research, analyse, draft, internal writes — do freely | `policy.json` maps 6 actions to `green`; runtime sets step `in_progress` **[V]** |
| 🟡 Yellow | sends, publishes, spend, signatures, settings, automations — draft then ask | `policy.json` maps 6 actions to `yellow`; runtime sets `awaiting_approval` and requires `--approver` + `--approval-ref` **[V]** |
| 🔴 Red | money movement, credentials, access changes, hard deletes, security bypass — never | `policy.json` maps 5 actions to `red`; runtime sets `blocked_human` and **cannot** be approved through any code path **[V]** |

So "autonomy" here means: **unbounded autonomy inside green, zero autonomy at yellow and
red.** That is a coherent and defensible design. The failure is that the *green* half —
where autonomy is permitted — has no execution machinery either.

### 2.3 Roles/agents intended

**[V]** 17 department agents exist as files in `.claude/agents/`, and all 17 are indexed
in `CLAUDE.md` §2 (enforced by `tests/core.sh` C3, which passes):

`chief-of-staff` · `cto-engineering` · `cpo-product` · `head-of-design` ·
`cmo-marketing` · `cro-sales` · `cfo-finance` · `clo-legal` · `chro-people` ·
`coo-operations` · `head-of-data` · `head-of-customer` · `customer-success` · `revops` ·
`security-grc` · `chief-knowledge-officer` · `rnd-tooling`

Each file carries YAML frontmatter (`name`, `description` with few-shot routing
examples) and a body with **Charter** (scope, inputs→outputs, success, decision rights,
loops & handoffs), **Rules**, and **Output**. `chief-of-staff.md` is the richest:
it names a 5-step operating procedure (Recall → Classify → Decompose → Harness →
Verify & record).

### 2.4 How roles are expected to coordinate

**[D]** Three documented mechanisms, in `company/playbooks.md` and `company/operating-model.md`:

- **Task contract** — a 10-field handoff envelope (objective · context · inputs ·
  constraints · expected output · acceptance criteria · decision authority · risks ·
  checkpoint · escalation). The receiver may reject, return, or escalate.
- **Five controlled loops** — Goal, Decision, Execution, Checkpoint, Validation &
  Improvement; each with an explicit exit, iteration cap, and escalation trigger.
- **Conflict & escalation logic** — §6 of `operating-model.md`.

**[V]** A *partial* executable form of coordination exists: `company_runtime.py`
`send-message` implements typed envelopes (`handoff|question|answer|feedback|decision`)
with hash-pinned payloads, participant authorization restricted to the step's
owner/checker plus adjacent DAG legs, and reverse-direction reply validation. This is the
single most sophisticated piece of code in the repo — and it is unreachable on Windows.

### 2.5 What should exist for the stated end state

**[I]** For a genuinely autonomous organization, on top of what is documented, the
following must exist as running code:

| Mechanism | Why it is required |
|---|---|
| Agent invoker | something must turn "step `produce-output` is ready, owner `cto-engineering`" into a model call and capture its output as evidence |
| Scheduler / daemon | something must notice a step became ready without a human typing a command |
| Event bus / trigger layer | inbound email, webhook, cron tick, or SLA breach must be able to *start* work |
| Planner | a natural-language goal must be decomposed into a validated workflow JSON automatically |
| Shared run state | one authoritative store both the API and the executor read and write |
| Long-term memory | facts learned in run N must be recallable in run N+1 |
| Failure recovery | a crashed or hung step must be detected, retried, or escalated without a human noticing first |
| Observability of *runs* | metrics on step latency, retry rate, approval wait, escalation rate |

### 2.6 Expected end-to-end autonomous loop

**[I]** Trigger (event/schedule/user goal) → Chief of Staff plans → workflow validated →
runtime marks ready steps → **executor dispatches each ready step to its owning agent** →
agent produces evidence → checker reviews → green steps auto-advance / yellow steps park
for human approval / red steps hand back → dependents release → validation loop scores
the output → lessons written to memory → run completes or escalates.

**[V] Steps that exist in code today: plan-validation, ready-state, policy gating,
evidence hashing, retry caps, cycle caps, maker-checker, typed messaging, terminal states.
Steps that do not exist at all: trigger, planner, executor, memory write-back, escalation
delivery, run observability.** The chain is broken at exactly the point where autonomy
would begin.

---

## 3. What Has Actually Been Built

### 3.1 Verified working

| Thing | Evidence |
|---|---|
| 17 agent role definitions, consistent structure, indexed | `.claude/agents/*.md`; `tests/core.sh` C3 passes |
| 13 local skills with SKILL.md | `.claude/skills/*/SKILL.md`; module suites pass |
| Three-colour policy classification | `runtime/policy.json` (17 actions) |
| HTTP API service — imports and is structurally sound | `python -c "import runtime.api"` → ok |
| HMAC-SHA256 short-lived tokens (≤900s), DB-bound roles, revocation | `runtime/auth.py:58-138` |
| SQLite store, 3 migrations, 18 tables, backup/restore/verify | `runtime/db.py`, `runtime/migrations/00{1,2,3}_*.sql` |
| Fail-closed connector admission (HTTPS-only, host allowlist, SSRF/private-IP denial, secret-ref-not-secret-value) | `runtime/connectors.py:27-73` |
| Webhook verification with timestamp skew + nonce replay defense | `runtime/connectors.py:83-107` |
| Exact-action-hash approvals, 15-min expiry, single-use consumption, human-only decision owner | `runtime/service.py:65-98`, `db.py:426-465` |
| Security headers, rate limiting, 256 KiB body cap, no-query-string routing, loopback-bind refusal | `runtime/api.py:32-48, 75-88, 388-389` |
| CI: compileall, acceptance suite, CodeQL, SBOM, npm audit, release-gate fail-closed check | `.github/workflows/ci.yml` |
| Next.js/Cloudflare control-center that calls the real API through a worker proxy | `apps/control-center/app/control-center.tsx:43,155,204,237`; `worker/index.ts:33` |
| systemd units + timers for backup and maintenance | `deploy/*.service`, `deploy/*.timer` |

### 3.2 Verified broken or inert

| Thing | Evidence |
|---|---|
| Workflow orchestrator — unusable on Windows | `import fcntl` → `ModuleNotFoundError` |
| Org state manager — unusable on Windows | same, `scripts/org_state.py:4` |
| Acceptance suite — fails on this host | 30 of 34 error; `SUITE: FAIL` |
| `CLAUDE.md` exceeds its own size budget | 12,592 bytes / ~250 lines vs. budget ≤9,000 bytes, ≤170 lines. Two CORE tests fail |
| 116 namespaced skill references in agent files resolve to nothing in this repo | script over `.claude/agents/*.md`: 116 refs, **0** resolvable locally |
| `state/`, `memory/`, `runtime/runs/` all contain only `README.md` | `ls -A` — no run, goal, task, or memory has ever been recorded |
| `runtime/data/` (the DB path) does not exist | `ls runtime/data` → No such file or directory |
| Audit log has no writer | `grep -rn "audit-log.jsonl" --include=*.py --include=*.sh --include=*.ts` → **no hits**. Its 7 lines are hand-authored 2026-07-14 example data |
| 4 governance tests fail (DSR send gating, breach-notification gating, per-send demand-gen gating) | trust-compliance 2 fail, revenue-engine 1 fail |

### 3.3 The nature of the test suite — important

**[V]** `tests/run.sh` reports ~260 checks. Inspecting `tests/core.sh` and the module
suites, the overwhelming majority are `grep`/`[ -f ]` assertions over Markdown:
*"routing-map.md present"*, *"all six carry Red flags + Verification"*, *"north-star tree
section (body, not description)"*. These verify that **documentation says the right
words**. They are prose-conformance tests, not behaviour tests.

The genuine behaviour tests are `tests/module-controlled-runtime.sh`,
`module-maker-checker.sh`, `module-organization-management.sh`, and the three
`tests/test_*.py` files. **Every one of the bash behaviour suites fails on this host**,
and the Python ones cannot run (`No module named pytest`). Net: the passing count is
dominated by checks that cannot detect a broken product.

---

## 4. Autonomy Gap Analysis

Ordered by how directly each blocks autonomy.

**G1 — No agent execution mechanism. [V]**
Nothing converts a ready step into work. `request_step` merely flips a status string; a
human must then run `complete --evidence <path>` pointing at a file the human produced.
*Why it prevents autonomy:* the organization cannot take a single action on its own. This
is not a weakness in autonomy — it is the absence of the thing being audited.

**G2 — No trigger surface. [V]**
No cron, no webhook receiver that starts work (the webhook verifier authenticates
payloads but no route consumes them), no file watcher, no queue. `deploy/*.timer` runs
DB maintenance only. *Why it matters:* every run begins with a human typing a command, so
the org is reactive-to-humans by construction. Documented cadences (`CLAUDE.md` §5) are
instructions to a human to configure Claude Code's `schedule` skill — outside this repo.

**G3 — No planner / decomposition. [V]**
Workflows are hand-authored JSON. Only two exist, both named "gold-run" proof artifacts.
`validate_workflow` can check a plan but nothing can *write* one. *Why it matters:* the
north-star ("say handle our Q3 board deck and the right part of the company acts")
requires goal→DAG synthesis. That mapping lives only in a prompt.

**G4 — Two competing state systems, neither authoritative. [V]**
JSONL runs (`runtime/runs/*.jsonl`) vs. SQLite runs (`runs`/`events` tables). The API
cannot read step state; the CLI cannot read approvals. *Why it matters:* an autonomous
loop needs one place to ask "what is ready, who owns it, what is blocked". Today there
is no such place, and any executor built now would have to pick a side — or the drift
doubles.

**G5 — Runtime is platform-locked away from the developer. [V]**
`fcntl` on Windows. *Why it matters:* the only executable governance in the product
cannot be exercised, tested, or extended locally. Every change to it is unverifiable
here, which is why 30 errors sit unaddressed.

**G6 — 116 of ~129 agent capabilities do not exist in this repo. [V]**
`cto-engineering` routes to `engineering:code-review`, `head-of-data` to `data:analyze`,
etc. None ship here and none are declared as a dependency (no `plugin.json`, no
`.mcp.json`, no marketplace manifest). *Why it matters:* a role whose skills resolve to
nothing degrades to a generic prompt. Role specialization — a named requirement of your
end state — is ~10% real. `chief-of-staff.md` even hedges: *"Confirm a named skill is
available before relying on it; otherwise use the charter... in declared degraded mode"*
— i.e. the design anticipates operating almost entirely degraded.

**G7 — No memory write-back or recall. [V]**
`memory/` holds only a README. `company/memory-and-learning.md` specifies a
propose→human-approve→reuse protocol and points at `productivity:memory-management` — a
skill not in this repo (G6). `company/lessons.md` has no entries added by any run. *Why
it matters:* without memory the org re-solves the same problem every session; the
Validation & Improvement loop has nowhere to deposit what it learned.

**G8 — Audit log is decorative. [V]**
No code appends. The `audit-log` skill instructs an agent to run an ad-hoc heredoc
`python3 - <<'EOF'`. *Why it matters:* the governance model's evidentiary backbone
depends on a model choosing to write correctly-shaped JSON by hand. An autonomous system
that self-reports its own compliance, unenforced, provides no assurance.

**G9 — Approval has no delivery channel or SLA. [V]**
A yellow step parks at `awaiting_approval` and nothing notifies anyone. `send_message`
carries agent↔agent messages only; there is no agent→human path. No timeout, no
reminder, no auto-escalation. *Why it matters:* the human is the scheduler, so the
organization's throughput equals human polling frequency — the "approval-latency
bottleneck" the project's own ledger logs as **OS-2, DEFERRED**.

**G10 — No run observability. [V]**
`runtime/observability.py` (76 lines) exposes HTTP metrics only — request count, status,
duration. Zero metrics on runs, steps, retries, approval wait, escalations.
`deploy/prometheus-alerts.yml` alerts on API health. *Why it matters:* you cannot operate
an autonomous org you cannot see. Nobody would know a run had been stuck for a week.

**G11 — No liveness, timeout, or recovery for in-flight work. [V]**
Retry is *reactive*: it only advances when someone calls `fail`. A step stuck
`in_progress` stays there forever — no heartbeat, no lease, no timeout, no dead-letter.
*Why it matters:* autonomous systems fail by hanging far more often than by erroring.

**G12 — Validation is prompt-only. [V]**
`validate` is a policy action string and a documented loop. No code scores an output
against acceptance criteria. `verify_submission` only re-hashes a file to prove it did
not change — it never asks whether the content is correct.

**G13 — No learning loop. [V]**
Loop 5 (Validation & Improvement) is documented; nothing computes or records a quality
signal, and `lessons.md` is append-by-hand.

**G14 — Tool permissions unenforced for agents. [V]**
Every agent's registration says "Tools: All tools". The connector gateway constrains
*connectors*, but an agent's ability to read files, run bash, or reach MCP servers is
unrestricted. `.claude/settings.local.json` is 173 bytes. *Why it matters:* least
privilege per role is a stated requirement and is absent.

**G15 — Live connectors blocked; only a fixture exists. [V, acknowledged]**
`fixture.json` targets `https://fixture.invalid`. `FixtureConnectorGateway.execute`
refuses anything where `kind != "fixture"`. No OAuth, no HTTP adapter. Ledger row 0.1
records this as **BLOCKED-ON-HUMAN**. *Why it matters:* the org has no hands. Every
department "works from files, pasted data, and manual input" (`CLAUDE.md` §7).

**G16 — Governance drift between prose and tests. [V]**
Four gating tests fail (DSR sends, breach notifications, per-send demand-gen), meaning
skill text no longer states gates the suite requires — inside the one subsystem
(governance) the product treats as non-negotiable.

**G17 — Control Center is read-mostly and unverified end-to-end. [V/D]**
It calls real endpoints (`/v1/ui-state`, `/v1/projects`), so it is more than a mock. But
there is no approvals view, no run/step view, and no queue actions; ledger row OS-12
records **PARTIAL — signed-in read-only release candidate**. The human approver has no UI
for the one job only they can do.

---

## 5. Complete Project Tracker

Status key: **✅ Completed** · **🟡 Partial** · **⛔ Not Started** · **🚧 Blocked** · **❓ Unknown**

### 1. Core Architecture

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| ARCH-01 | One state architecture | ✅ | Roles settled, not merged: the JSONL log is the system of record for **execution**; SQLite is identity/orgs + the operator **read model**. `runtime/projection.py` mirrors runs one way (log→store, never back) via migration `004`; the sweep mirrors automatically, and only when a store is configured | `apps/control-center` still reads the old API shape | — | ~~P0~~ done | 15 projection tests; live run visible in both halves |
| ARCH-03 | Organization is a runtime boundary | ✅ | Runs carry `org_id` (`--org`, `MYORG_ORG_ID`, default `default`); memory is org-scoped; the projection creates the org and bootstraps a `runtime-projector` service actor | Leases and evidence paths are not yet org-scoped | — | P1 | `test_one_organizations_runs_are_not_visible_to_another` |
| ARCH-02 | Cross-platform runtime | ✅ | `runtime/filelock.py` — bounded polling `exclusive_lock`, `fcntl` on POSIX / `msvcrt` on Windows; test harness converts MSYS paths via `cygpath -m` | — | — | ~~P0~~ done | 5/5 `tests.test_filelock`; all 12 shell modules 0 failed |
| ARCH-03 | Policy classification | ✅ | 17 actions → green/yellow/red | — | — | — | `runtime/policy.json` |
| ARCH-04 | Constitution within budget | ⛔ | 12,592 B / ~250 lines | 3,592 B over; move detail into `company/` | — | P2 | `tests/core.sh` C1 fails |
| ARCH-05 | Declared external dependencies | ⛔ | Nothing declares the 116 external skills | `plugin.json`/`.mcp.json`/marketplace manifest | — | P0 | no manifest in repo |

### 2. Agent / Role System

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| AGENT-01 | 17 role definitions | ✅ | Frontmatter + charter + rules + output | — | — | — | `.claude/agents/*.md` |
| AGENT-02 | Decision authority per role | ✅ | Decide/Consult/Escalate in each charter | Machine-readable form the runtime can read | — | P1 | e.g. `chief-of-staff.md` Charter |
| AGENT-03 | Role↔index integrity | ✅ | Enforced by test | — | — | — | `core.sh` C3 passes |
| AGENT-04 | Runtime-verified ownership | ✅ | `agent_exists()` gates workflow owners/checkers | — | — | P1 | `company_runtime.py:50,70` |
| AGENT-05 | Agent execution (invoker) | ✅ | `runtime/executor.py` — claims ready steps, dispatches to the owning agent via `claude -p` (tools disabled), writes output as hashed evidence, calls `complete`; pluggable backend with a token-free `StubBackend` | Green-path only: no checker/maker-checker driving, no scheduler | — | ~~P0~~ done | 9/9 `tests.test_executor`; real run `gold-auto-01` completed 3 steps with no human input |
| AGENT-08 | Maker-checker driving | ✅ | `drive_check()` — checker reads the hash-verified submission, returns `VERDICT: APPROVE/RETURN/REJECT`, verdict filed as a typed message then applied via `check_*`; unreadable verdict is treated as RETURN, never approval | — | — | ~~P0~~ done | 7 check tests; live `gold-mc-03` approved autonomously |
| AGENT-09 | Agent context / handoff payload | ✅ | `upstream_handoffs()` passes each direct dependency's evidence into the prompt, re-hashed before it is trusted and clipped at 6,000 chars | Direct edges only (deliberate: matches COORD-02) | — | ~~P0~~ done | 5 handoff tests; live run `gold-auto-02` — COO wrote "I accept the CTO's qualification of A1 and I build on it" |
| AGENT-06 | Per-role tool permissions | ⛔ | All agents get all tools | Least-privilege allowlist per role | — | P1 | agent registrations |
| AGENT-07 | Agent-file → registration parity | 🟡 | 17 files, 17 registered | No test that charters stay in sync | — | P2 | `core.sh` C3 |

### 3. Skills & Capabilities

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| SKILL-01 | In-repo skills | ✅ | 13 skills, all with `SKILL.md`, and now all claimed by a department | — | — | — | `.claude/skills/`; 0 orphans |
| SKILL-02 | External skill references | ✅ | `runtime/skills.py` resolves every claimed skill to in-repo, declared-external, or unresolved; `company/skills.manifest.json` declares 124 externals with family + provider, each `verified_here: false`. Real count is **137 distinct, not 116**. `--check` fails on any undeclared reference | Declared ≠ proven: none has been invoked here | SKILL-03 | ~~P0~~ done | 14 tests; `module-skills.sh` |
| SKILL-06 | Skill families to build | 🚧 | 124 externals grouped into **17 families** (data 10, engineering 10, human-resources 9, legal 9, operations 9, product-management 9, sales 9, ungrouped 9, …) — a work plan, not 124 tickets | Each family needs building or a verified provider | SKILL-02 | P1 | `company/skills.manifest.json` families |
| SKILL-03 | Skill→executable-tool binding | 🟡 | `skills.py --tools` binds each in-repo skill to the scripts its own instructions name, and the suite fails if one points at a file that is not there. Honest result: **1 of 13** skills runs anything; 12 are written procedure | The 12 need real tools, or to be accepted as procedure | SKILL-06 | P1 | `runtime/skills.py --tools` |
| SKILL-04 | Governance text matches tests | ⛔ | 4 gating assertions fail | Restore DSR/breach/per-send gate wording | — | P1 | trust-compliance, revenue-engine failures |
| SKILL-05 | Skill discipline sections | ✅ | Red flags + Verification in each | — | — | — | V5/T8 pass |

### 4. Task & Workflow Engine

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| WF-01 | Workflow schema + validator | ✅ | version, ids, owners, actions, deps, cycle detection, attempt caps | — | — | P0 | `validate_workflow()` |
| WF-02 | DAG ready-state | ✅ | `release_dependents()` | — | — | P0 | `company_runtime.py:222` |
| WF-03 | Append-only hash-chained events | ✅ | seq + prev_hash + event_hash verified on read | — | — | P0 | `read_events()`, `append_event()` |
| WF-04 | Idempotent mutations | ✅ | `request_id` replay detection | — | — | P1 | `mutate():150-153` |
| WF-05 | Retry & cycle caps | ✅ | `max_attempts` 1–5, `max_cycles` 1–100 | Reactive only — no timeout | ARCH-02 | P1 | `fail()`, `mutate():156` |
| WF-06 | Evidence contract | ✅ | Repo-relative path + SHA-256, re-verified at check | Content validation (only integrity) | — | P1 | `evidence_path()`, `verify_submission()` |
| WF-07 | Stale-revision protection | ✅ | `complete` requires workflow revision | — | ARCH-02 | — | `complete():238` |
| WF-08 | Automatic task creation | ✅ | `runtime/planner.py` — Chief of Staff decomposes a plain-words goal into a DAG; output is checked by the real `validate_workflow` and errors are handed back for repair (3 attempts) | Planner cannot see live company data, so plans are structural not data-informed | — | ~~P0~~ done | 9 tests; live `fix-onboarding` = 18 steps, 10 departments, valid first try |
| WF-09 | Workflow library | ⛔ | 2 gold-run proofs | Real business workflows | WF-08 | P1 | `runtime/workflows/` |
| WF-10 | Task tracker (goal/task/decision) | ⛔ | `org_state.py` written but inert here | Ever being used | — | P1 | `state/` empty; 3 tests fail |
| WF-11 | Prioritization | ⛔ | Nothing | Priority/queue ordering across runs | ARCH-01 | P2 | — |
| WF-12 | Termination conditions | ✅ | 6 terminal states incl. cycle/retry/review limits | — | ARCH-02 | — | `TERMINAL`, `blocked_*` |

### 5. Hooks / Events / Triggers

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| HOOK-01 | Claude Code hooks | ⛔ | No `hooks` config | PreToolUse/PostToolUse enforcement of §3 | — | P1 | `.claude/` has no hooks |
| HOOK-02 | Inbound webhook trigger | 🟡 | Verifier built, **no route** | Endpoint + event→run mapping | ARCH-01 | P1 | `WebhookVerifier` unused by `api.py` |
| HOOK-03 | Scheduled cadences | ⛔ | Prose in `CLAUDE.md` §5 | In-product scheduler | AGENT-05 | P1 | no cron/schedule in code |
| HOOK-04 | SLA/threshold triggers | ⛔ | SLA described in `lead-response` | Clock + breach event | HOOK-03 | P1 | — |
| HOOK-05 | Maintenance timers | ✅ | systemd hourly/daily | Linux-only; DB chores only | — | P2 | `deploy/*.timer` |

### 6. Autonomous Execution Loops

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| LOOP-01 | Five controlled loops (spec) | ✅(doc) | Goal/Decision/Execution/Checkpoint/Validation with caps | No executable form | — | P1 | `operating-model.md` |
| LOOP-02 | Execution loop (code) | ✅ | `advance()` drives to quiescence, bounded by `MAX_ITERATIONS` | Single-shot; not yet a daemon or scheduled loop | HOOK-03 | P1 | `test_the_driver_never_loops_forever` |
| LOOP-03 | Scheduled cadences | ✅ | `runtime/scheduler.py` — sweeps every movable run and drives it; bounded on passes, interval and per-run iterations; one broken run never stops the rest | No cron/calendar triggers; no daemon install | HOOK-03 | ~~P0~~ done | 6 sweep tests |
| LOOP-04 | Feedback loop | ✅ | Checker's reasons are fed into the maker's next attempt (`last_feedback`), hash-verified; one artifact per review cycle so earlier hashes stay valid | Run-level feedback (across steps) still absent | — | ~~P1~~ done | live `gold-mc-02` round 2: "the five gaps are now written as code" |
| LOOP-05 | Learning loop | ⛔ | `lessons.md` (hand-edited) | Signal capture + write-back | MEM-02 | P2 | no writer |
| LOOP-06 | Checkpoint loop | ⛔ | Documented caps | Time/cost budget enforcement | — | P2 | `operating-model.md` §4 |

### 7. Memory & State

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| MEM-01 | Run state (short-term) | 🟡 | JSONL per run, replayable | Never exercised | — | P0 | `runtime/runs/` = README only |
| MEM-02 | Long-term business memory | ⛔ | `memory/README.md` | Store, write path, recall path | SKILL-02 | P1 | dir empty |
| MEM-03 | Memory protocol | ✅(doc) | propose→approve→reuse; 4-class data table | Enforcement | — | P1 | `memory-and-learning.md` |
| MEM-04 | Org state (goal/task/decision) | ⛔ | `org_state.py` inert | Adoption | — | P1 | `state/` = README only |
| MEM-05 | Context propagation | ✅ | Hash-verified upstream evidence in every dispatch | Direct dependencies only | — | ~~P1~~ done | `test_a_step_receives_the_work_of_the_step_it_depends_on` |
| MEM-06 | Cross-run shared memory | ✅ | `runtime/memory.py` — append-only hash-chained store, org-scoped. Agents *propose*; only human-approved entries are recalled into prompts. A checker RETURN/REJECT auto-proposes a lesson; the console has a "Things to remember" queue | Keyword recall, not semantic; no decay | — | ~~P0~~ done | 17 tests; live: lesson approved in one run reached a different run's prompt |
| MEM-07 | Project intake persistence | ✅ | `project_intakes`, 6 doc controls, ready-gate | — | — | — | `service.py:222-264` |

### 8. Inter-Agent Coordination & Handoffs

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| COORD-01 | Typed message envelopes | ✅ | 5 kinds, subject cap, classification, hashed payload | — | — | P1 | `send_message()` |
| COORD-02 | Participant authorization | ✅ | Same-step or adjacent-DAG-edge only | — | — | P1 | `company_runtime.py:270-281` |
| COORD-03 | Reply-direction integrity | ✅ | Reply must reverse same-step direction | — | — | P2 | `:283-286` |
| COORD-04 | Maker-checker | ✅ | Distinct checker, green-only, review caps, immutable submissions — now driven autonomously | — | — | ~~P0~~ done | `module-maker-checker.sh` + `drive_check` tests |
| COORD-05 | Task contract (executable) | ⛔ | 10-field contract in prose | Schema + validation at handoff | ARCH-01 | P1 | `playbooks.md` |
| COORD-06 | Agent→human channel | ✅ | `runtime/notify.py` — an outbox of notices (blocking / attention / routine), deduped per subject. Sending outward stays a yellow action: with no `MYORG_NOTIFY_COMMAND` configured it lists and never sends | One local operator; no per-person routing | — | ~~P0~~ done | 12 tests incl. a real delivery command |
| COORD-07 | Cross-department playbooks | ✅(doc) | Inbound-lead, dunning, renewal, churn, vendor | Not executable | WF-09 | P1 | `playbooks.md`; V7 passes |

### 9. Tools & Integrations

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| TOOL-01 | Connector admission control | ✅ | HTTPS-only, host allowlist, SSRF denial, secret-ref rule | — | — | — | `validate_manifest()` |
| TOOL-02 | Connector authorization lifecycle | ✅ | authorize/revoke/kill-switch, ≤366d expiry, scope caps, human-only | — | — | — | `service.py:125-172`, mig 003 |
| TOOL-03 | Execution gateway | 🟡 | Fixture only; refuses non-fixture | Real HTTP/OAuth adapter | TOOL-02 | P1 | `FixtureConnectorGateway:119` |
| TOOL-04 | Live connectors | 🚧 | `fixture.invalid` | Human OAuth authorization | TOOL-03 | P1 | ledger 0.1 |
| TOOL-05 | Idempotent execution + receipts | ✅ | Key reuse conflict; atomic approval-consume + receipt | — | — | — | `db.py:627-665` |
| TOOL-06 | Receipt reconciliation | ✅ | provider_status + details hash; unreconciled endpoint | Nothing produces real receipts | TOOL-04 | P2 | mig 003 |
| TOOL-07 | Webhook ingestion route | ⛔ | Verifier exists, unwired | Endpoint | HOOK-02 | P1 | no `/webhooks` route |
| TOOL-08 | MCP server config | ⛔ | None in repo | `.mcp.json` for department systems | ARCH-05 | P1 | `company/connectors.md` describes, repo has none |

### 10. Planning & Decision-Making

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| PLAN-01 | Routing map | ✅(doc) | 222-line request→skill catalog | Most targets unresolvable | SKILL-02 | P1 | `routing-map.md` |
| PLAN-02 | Goal decomposition | ✅ | Same planner; assigns owners from the 17 real agent files and actions from `policy.json`, and is told which actions stop for a human | No re-planning mid-run when a step fails | — | ~~P0~~ done | `fix-onboarding.json`: 15 green / 3 yellow, outward steps last |
| PLAN-03 | Decision record | ⛔ | `org_state.py decision` inert | Adoption | — | P1 | `state/` empty |
| PLAN-04 | Dependency management | ✅ | depends_on + cycle detection + release | — | — | P1 | `validate_workflow()`, `release_dependents()` |
| PLAN-05 | Conflict resolution | ✅(doc) | §6 escalation logic | Not executable | — | P2 | `operating-model.md` |

### 11. Monitoring / Observability

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| OBS-01 | Structured JSON logs | ✅ | `JsonFormatter`, per-request log w/ trace+actor | — | — | — | `observability.py`, `api.py:202` |
| OBS-02 | Prometheus metrics | 🟡 | HTTP-only, token-protected `/metrics` | Run/step/approval metrics | ARCH-01 | P1 | `Metrics`, `api.py:217` |
| OBS-03 | Alerting | 🟡 | API health alerts | Stuck-run, approval-age, escalation alerts | OBS-02 | P1 | `deploy/prometheus-alerts.yml` |
| OBS-04 | Run health | ✅ | `runtime/health.py` — running / waiting on you / stalled / finished / failed, worst first; a run whose record is unreadable is reported FAILED, never silently dropped | — | — | ~~P0~~ done | 7 health tests |
| OBS-05 | Stall detection | ✅ | `stalled` is its own state: nothing movable, or ready work idle past `STALLED_AFTER_MINUTES` | No alerting — you must look | — | ~~P0~~ done | `test_a_quiet_run_with_work_left_is_reported_stalled` |
| OBS-06 | Operational events (DB) | ✅ | `operational_events` chain on UI/project/connector ops | Not linked to runs | ARCH-01 | P1 | `db.py:186-210` |

### 12. Validation / QA

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| VAL-01 | Evidence integrity | ✅ | SHA-256 re-verify at check | — | ARCH-02 | — | `verify_submission()` |
| VAL-02 | Output quality validation | ✅ | Two gates before evidence is written: a free structural gate (refusals/questions/stubs rejected) and an opt-in grader against a step's `acceptance` criteria (`VERDICT: MEETS/FAILS`); rejection feeds the reason into the retry | Grader is the same model family as the author | — | ~~P0~~ done | 6 gate tests; live `gold-graded-01` rejected then passed on retry |
| REC-08 | Approved steps get finished | ✅ | `advance()` now drives `in_progress` steps — before this, a step a human approved was never picked up again and the run stalled forever | — | — | ~~P0~~ done | `test_a_finished_run_is_never_driven_again` |
| DEBT-01 | `executor.py` is oversized | ✅ | Split into `prompts.py` (what agents are told), `backends.py` (how they are reached), `checking.py` (independent review); executor is 274 lines | `db.py` 777, `api.py` 411, `company_runtime.py` 367 are still over — pre-existing | — | P2 | 138/138 tests green through the refactor |
| VAL-03 | Independent review | ✅ | Maker-checker with distinct checker | — | — | — | `module-maker-checker.sh` |
| VAL-04 | Release gate | ✅ | Fail-closed; template must be blocked in CI | — | — | — | `scripts/release_gate.py`, ci.yml |
| VAL-05 | Release evidence + SBOM | ✅ | Generated + secret scan (incl. `sk-` pattern) | — | — | — | `scripts/release_evidence.py` |

### 13. Error Recovery & Resilience

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| REC-01 | Bounded retry | ✅ | Attempt budget → `blocked_retry_limit`, and the failure reason is now fed into the next attempt | Reactive only | — | ~~P1~~ done | `test_the_rejection_reason_is_shown_on_the_retry` |
| REC-02 | Step timeout / liveness | ✅ | `runtime/leases.py` — claiming grants a bounded lease, heartbeats renew it, and `reclaim()` fails abandoned steps back into the retry budget; the scheduler reclaims on every sweep | Lease file sits outside the hash chain | — | ~~P0~~ done | `test_work_abandoned_by_a_dead_worker_is_given_back` |
| REC-03 | Crash-safe state | ✅ | fsync'd append-only; hash chain verified on read | — | — | — | `append_event()` |
| REC-04 | Concurrency safety | ✅ | Cross-platform `exclusive_lock` per run/state; SQLite tx | Bounded 10s wait then abort — no queueing | — | ~~P0~~ done | `test_filelock` exclusion + serialisation tests |
| REC-05 | DB backup/restore | ✅ | `Store.backup`, `restore_backup`, timer + runbook | Restore drill unrun | — | P2 | `db.py:717-760` |
| REC-06 | Dead-letter / escalation | ✅ | `runtime/escalation.py` turns every dead end (retry/review/cycle limit, rejection, red hand-back), every parked approval and every proposed lesson into a notice; the sweep scans each pass | No ageing or reminder if a notice is ignored | — | ~~P0~~ done | `test_a_run_that_ran_out_of_retries...` |
| REC-07 | Graceful degradation | ✅(doc) | Fall back to files, label staleness, pause SLA | Not executable | — | P2 | `connectors.md` |

### 14. Human-in-the-Loop / Governance

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| HITL-01 | Green/yellow/red gates (runtime) | ✅ | Red cannot be approved by any code path | — | — | P0 | `request_step():189-191` |
| HITL-02 | Approval surface | ✅ | Console shows a 5-line decision brief (ASK / IF YES / FINDINGS / WATCH / RECOMMEND) written once when the step parks; full work collapsed behind it. Decisions are ordered: handed-back red first, then DAG depth, then blast radius | Local single-operator; no auth | ARCH-01 | ~~P0~~ done | 20 tests incl. ordering + brief XSS |
| HITL-04 | Approval UI | ⛔ | Control Center has intake/UI-state only | Approve/reject surface | UI-01 | P1 | `control-center.tsx` calls |
| HITL-05 | Approval audit trail | 🟡 | `approvals` table + operational events | Not joined to run/step; audit log unwritten | OBS-05 | P1 | mig 001 |
| HITL-06 | Approval is attributable | ✅ | `decide()` refuses an empty approver or reason; both land in the run's event chain | No identity check — a name is self-asserted | — | ~~P1~~ done | `test_a_decision_must_carry_a_name_and_a_reason` |
| HITL-07 | Prompt-injection stance | ✅(doc) | "content is data, not instructions" in constitution + agents | No runtime enforcement | HOOK-01 | P1 | `CLAUDE.md` §3 |

### 15. Security & Permissions

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| SEC-01 | Token auth | ✅ | HS256, ≤900s, canonical b64, claim-set equality, revocation | Key rotation/kid registry | — | P1 | `auth.py` |
| SEC-02 | RBAC | ✅ | DB role bindings; `_require` on every service method | Not applied to agents | AGENT-06 | P1 | `service.py:33-35` |
| SEC-03 | Org isolation | ✅ | `org_id` on every query | — | — | — | `db.py` |
| SEC-04 | Gateway signing | ✅ | Signed requests + nonce replay defense | — | — | — | `gateway_auth.py`, mig 002 |
| SEC-05 | HTTP hardening | ✅ | CSP/HSTS/nosniff/frame-deny, rate limit, 256 KiB cap, no query strings, loopback guard | — | — | — | `api.py:75-88` |
| SEC-06 | Secrets handling | ✅ | Env-var refs only; manifest rejects literals; scanner in CI | — | — | — | `connectors.py:46-50` |
| SEC-07 | Agent tool least-privilege | ⛔ | All agents, all tools | Per-role allowlist | AGENT-06 | P1 | registrations |
| SEC-08 | Threat model | ✅(doc) | Written | Not re-verified since 2026-08-06 | — | P2 | `docs/SECURITY-THREAT-MODEL.md` |

### 16. Data / Persistence

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| DATA-01 | Schema + migrations | ✅ | 3 migrations, 18 tables, versioned | Never instantiated (`runtime/data` absent) | — | P1 | `migrations/`, `db.migrate()` |
| DATA-02 | Integrity verification | ✅ | `Store.verify()`; `/readyz` | — | — | — | `db.py:676` |
| DATA-03 | Retention/purge | ✅ | `purge-transient`, 30d idempotency default | — | — | — | `admin.py`, `maintain_runtime.sh` |
| DATA-04 | Data classification | ✅(doc) | 4 classes; restricted never stored | Runtime accepts public/internal only — enforced | — | — | `service.py:58` |
| DATA-05 | Run-state durability | ✅ | **"One store" is superseded by ARCH-01** (two systems, defined roles). The real gap was that the log side had no integrity check or recovery: `runtime/durability.py` verifies every event chain, re-hashes every evidence file, checks each memory store, and does backup/restore with a manifest | Backups are manual; no schedule | ARCH-01 | ~~P0~~ done | 10 tests; live backup of 4 runs verified |
| DATA-06 | Sidecar files are not runs | ✅ | A run id must start with a letter, so `_`-prefixed files in the runs directory are sidecars; `core.run_files()` is the one place that decides | — | — | ~~P1~~ done | the outbox was being escalated as a broken run until this |

### 17. APIs / Backend

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| API-01 | HTTP boundary | ✅ | stdlib ThreadingHTTPServer, 16 routes, typed errors | — | — | — | `api.py` |
| API-02 | Run endpoints | 🟡 | `runs` now carries real status/cycles + a `run_steps` table with owner, risk, status, evidence hashes, dependencies | `api.py` does not yet expose the new step rows over HTTP | ARCH-01 | P1 | `004_run_projection.sql`; `waiting_on_humans()` |
| API-03 | Approval endpoints | ✅ | create + decide, exact hash | Not tied to workflow steps | ARCH-01 | P0 | `api.py:249-256` |
| API-04 | Connector endpoints | ✅ | inventory/status/authorization/execute/reconcile | Fixture-only execution | TOOL-04 | P1 | `api.py:257-297` |
| API-05 | Project + UI-state endpoints | ✅ | CRUD w/ optimistic concurrency | — | — | — | `api.py:298-327` |
| API-06 | OpenAPI spec | ⛔ | None | Machine-readable contract | — | P2 | — |
| API-07 | Agent-facing execution API | ✅ | `runtime/agent_api.py` — `GET /v1/work`, `POST /v1/claim` (returns the full prompt + lease), `/v1/submit`, `/v1/heartbeat`, `/v1/fail`, `GET /v1/health`. Bearer token ≥32 chars required or it refuses to start; localhost only. Submitted work faces the same structural + acceptance gates as in-process work | No per-agent identities — one shared token | — | ~~P0~~ done | 22 tests; live worker claimed, heartbeat, submitted over HTTP |

### 18. UI / Operator Experience

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| UI-01 | Control Center app | 🟡 | 648-line React view, worker proxy, real API calls | Approvals, runs, queue actions | API-02 | P1 | `control-center.tsx` |
| UI-02 | Rendered-HTML test | ✅ | `node --test` in CI | Only render-level | — | P2 | `tests/rendered-html.test.mjs` |
| UI-03 | Accessibility | 🟡 | Structure passes local checks | Human UAT unrun | — | P2 | ledger OS-12 |
| UI-04 | Org chart | ✅ | Static HTML | Reflects files, not live state | — | P2 | `org-chart.html` |
| UI-05 | Approval queue UI | ⛔ | — | The human's core surface | HITL-04 | P1 | — |

### 19. Testing

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| TEST-01 | Acceptance suite runs green | 🟡 | 12/12 shell modules + 39/39 Python tests pass | Only CORE's two `CLAUDE.md` size checks fail | DOC-07 | P2 | `bash tests/run.sh` |
| TEST-02 | Behaviour vs. prose ratio | 🟡 | ~85% grep-over-Markdown | Behaviour coverage of real flows | AGENT-05 | P1 | `core.sh` inspection |
| TEST-03 | Python tests runnable | ⛔ | 3 files, 608 lines | `pytest` absent; no requirements/venv | — | P1 | `No module named pytest` |
| TEST-04 | Executor tests | ✅ | 9 behaviour tests on real runs via `StubBackend` — never calls a model | No test of the real CLI backend (would spend tokens) | — | ~~P0~~ done | `tests/test_executor.py`, `tests/module-executor.sh` |
| TEST-05 | Cross-platform CI | 🟡 | ubuntu-24.04 only | windows-latest matrix | ARCH-02 | P1 | `ci.yml` |
| TEST-06 | End-to-end gold run recorded | 🟡 | Three live runs from current HEAD: `gold-auto-02` (handoffs), `gold-mc-03` (maker-checker approved), `gold-graded-01` (graded, rejected then passed) — all halted correctly at the yellow step | Artifacts gitignored and repo is not a git repo, so nothing is committed | — | P1 | `runtime/runs/gold-*` |
| TEST-07 | Test isolation / cleanup | ✅ | `Store.reading()` in `db.py` closes read connections; test-side `sqlite3.connect` wrapped in `closing()` with explicit commits | — | — | ~~P1~~ done | 39/39 Python tests pass; 0 temp dirs leaked across a full run |

### 20. Deployment / Infrastructure

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| DEP-01 | systemd units + timers | ✅ | api, backup, maintenance | Linux-only | — | P2 | `deploy/` |
| DEP-02 | Reverse proxy example | ✅ | Config sample | — | — | P2 | `reverse-proxy.example.conf` |
| DEP-03 | Env template | ✅ | `myorg.env.example` | — | — | — | `deploy/` |
| DEP-04 | UI hosting | 🟡 | Cloudflare worker + wrangler | Never deployed/verified | — | P2 | `worker/index.ts` |
| DEP-05 | Deploy + rollback evidence | ⛔ | Runbook only | Executed drill | — | P2 | ledger OS-13 |
| DEP-06 | Dependency manifest (Python) | ⛔ | No `requirements.txt`/`pyproject.toml` | Pin toolchain incl. pytest | — | P1 | repo root |

### 21. Documentation

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| DOC-01 | Constitution | 🟡 | Clear, but over budget | Trim 3.5 KB | ARCH-04 | P2 | `CLAUDE.md` |
| DOC-02 | Company handbook | ✅ | 7 files, 659 lines | — | — | — | `company/` |
| DOC-03 | Gap ledger | ✅ | Honest dispositions incl. DEFERRED/BLOCKED | Stale vs. current failures | — | P1 | `docs/GAP-LEDGER.md` |
| DOC-04 | Runtime audit + RCA | ✅ | Explicit exclusions | — | — | — | `docs/RUNTIME-AUDIT.md` |
| DOC-05 | Templates | ✅ | agent, skill, intake, release-evidence | — | — | — | `templates/` |
| DOC-06 | Autonomy target decided | ✅ | User ruled 2026-09-01: autonomous org with exception-only HITL (see §9) | Repo docs still describe permanent HITL as the goal — they describe today | — | ~~P0~~ done | §9 Settled Decisions |
| DOC-07 | CLAUDE.md within size guardrail | ⛔ | 222 lines / 12,592 bytes | Suite caps it at 170 lines / 9,000 bytes; CORE fails 2 checks. Trim or raise the cap deliberately | — | P1 | `core.sh`; `wc -lc CLAUDE.md` |
| DATA-07 | SQLite connections are closed | ✅ | `Store.reading()`; 14 leaking `with self.connect()` blocks fixed — sqlite3's context manager commits but never closes | — | — | ~~P1~~ done | `grep 'with self.connect()' runtime/db.py` → none |

### 22. Production Readiness

| ID | Deliverable | Status | What Exists | What Is Missing | Deps | Pri | Evidence |
|---|---|---|---|---|---|---|---|
| PROD-01 | Green test suite | 🟡 | Everything green except the `CLAUDE.md` size guardrail | DOC-07 | DOC-07 | P2 | `bash tests/run.sh` |
| PROD-02 | Production IdP | 🚧 | Local HMAC tokens | Managed identity | — | P1 | ledger OS-7 |
| PROD-03 | Live monitoring | ⛔ | Config only | Deployed stack | OBS-02 | P2 | — |
| PROD-04 | UAT | ⛔ | Runbook | Executed | — | P2 | `docs/UAT-*.md` |
| PROD-05 | Never-run system | ✅ | System has now really run: 4 live multi-agent runs, evidence and event chains on disk | `state/` and `memory/` still unused by the executor | MEM-04 | P1 | `runtime/runs/gold-*` |

---

## 6. Critical Path

### P0 — Autonomy blockers

| # | Item | Tracker IDs |
|---|---|---|
| 1 | Runtime cannot run on the dev machine | ARCH-02, TEST-01, REC-04 |
| 2 | No agent execution mechanism | AGENT-05, LOOP-02, LOOP-03, API-07, TEST-04 |
| 3 | Two competing state systems | ARCH-01, DATA-05, API-02, OBS-04 |
| 4 | No planner (goal → workflow) | PLAN-02, WF-08 |
| 5 | No agent→human channel (approvals/escalations are silent) | COORD-06, HITL-02, HITL-06, REC-06 |
| 6 | No liveness/timeout — `in_progress` is absorbing | REC-02 |
| 7 | Audit log has no writer | OBS-05 |
| 8 | 116 role capabilities unresolvable + undeclared | SKILL-02, ARCH-05 |
| 9 | Autonomy target contradicts documented design | DOC-06 |
| 10 | System has never actually run | PROD-05, TEST-06 |

### P1 — Major capability gaps

REC-01/REC-06 hardening · OBS-02/03/04 run telemetry · HITL-04 + UI-05 approval surface ·
MEM-02/04 memory & org state · AGENT-06 + SEC-07 per-role tool permissions ·
VAL-02 output validation · TOOL-03/04/07 live connectors + webhook route ·
COORD-05 executable task contract · SKILL-04 governance-text repair ·
TEST-03/05 pytest + Windows CI · DEP-06 dependency manifest · WF-09 real workflows

### P2 — Improvements / hardening

ARCH-04 constitution trim · LOOP-05/06 learning & budget loops · WF-11 prioritization ·
API-06 OpenAPI · UI-03 a11y UAT · DEP-04/05 deploy & rollback drill · REC-05 restore drill ·
PROD-03/04 monitoring + UAT

### Dependency order (recommended)

```
DOC-06  decide the target
   │
ARCH-02 Windows locking ──► TEST-01 suite green ──► TEST-06 first real gold run
   │
ARCH-01 one state store ──► API-02 run/step API ──► API-07 agent execution API
   │                                                      │
   │                                                 AGENT-05 executor
   │                                                      │
   ├──► REC-02 leases/timeouts ◄──────────────────────────┤
   ├──► OBS-05 audit writer ◄──────────────────────────---┤
   ├──► COORD-06/HITL-02 human channel ◄──────────────────┤
   │                                                      │
   └──► PLAN-02/WF-08 planner ◄───────────────────────────┘
                     │
        ARCH-05/SKILL-02 declare & resolve capabilities
```

**ARCH-02 gates everything** — you cannot verify a single change to the orchestrator on
this machine until it is fixed. It is also the cheapest item on the list.

---

## 7. 5-Why RCAs for P0 Blockers

### RCA-1 — Runtime cannot run on the developer's own platform

**Problem:** `runtime/company_runtime.py` and `scripts/org_state.py` fail at import on
Windows 11; 30 of 34 acceptance tests error; the suite is red.

- **Why 1:** Both `import fcntl`, which does not exist on Windows.
- **Why 2:** `fcntl.flock` was chosen for the concurrency guarantee (serialize mutations
  per run / per state file) with a hard "dependency-free" constraint — stdlib only.
- **Why 3:** The stdlib has no portable advisory file lock, so the POSIX primitive was
  taken without an abstraction boundary; the lock is called inline in `run_lock()` and
  `state_lock()` rather than behind an interface.
- **Why 4:** CI runs `ubuntu-24.04` only, so the platform assumption was never
  contradicted. Green CI was treated as proof the runtime worked.
- **Why 5 (root):** **The target execution environment was never stated as a
  requirement.** No manifest, no supported-platform note, no OS matrix. The code was
  written for where CI ran, not for where it runs.

**Evidence:** `ModuleNotFoundError: No module named 'fcntl'` on both files ·
`.github/workflows/ci.yml` single runner · no `requirements.txt`/`pyproject.toml` ·
`runtime/runs/` contains only `README.md` despite `RUNTIME-AUDIT.md` claiming the manual
gold run "passed locally" (it passed on *some* host, not this one).

**Root cause:** Unstated platform requirement, plus a non-portable primitive used
without an abstraction seam.
**Contributing:** stdlib-only constraint; single-OS CI; behaviour tests concentrated in
exactly the files that cannot load.
**Consequences:** The only executable governance in the product is unverifiable and
unextendable here. Any change to orchestration, maker-checker, or messaging ships blind.
**Corrective:** Extract locking into `runtime/filelock.py` with one `exclusive_lock(path)`
context manager — `fcntl.flock` on POSIX, `msvcrt.locking` on Windows — and have both
callers use it. Nothing else changes.
**Preventive:** Add `windows-latest` to the CI matrix; add a `requirements.txt` pinning
`pytest`; state supported platforms in `README.md`.
**Verify:** `python runtime/company_runtime.py validate runtime/workflows/manual-gold-run.json`
prints `workflow valid` on Windows; `bash tests/module-controlled-runtime.sh` and
`module-maker-checker.sh` and `module-organization-management.sh` pass on both OSes.
**Affects:** ARCH-02, TEST-01, TEST-05, REC-04, DEP-06, and unblocks WF-*, COORD-*, MEM-01/04.

---

### RCA-2 — No agent execution mechanism

**Problem:** Nothing turns a ready step into work. Steps advance only when a human types
a CLI command and supplies an evidence file they produced themselves.

- **Why 1:** No code invokes a model, spawns a process, or calls an agent. The runtime
  only mutates status strings.
- **Why 2:** This was **deliberate**. `docs/RUNTIME-AUDIT.md`: *"Excluded now:
  model/provider calls, automatic agent dispatch... The workflow is manual-first,"* and
  *"Provider-driven dispatch may be considered only after a human reviews a successful
  gold run."*
- **Why 3:** The sequencing rule — prove control before adding autonomy — placed the
  executor behind a human-review gate on the gold run.
- **Why 4:** That gate was never passed on the record: `runtime/runs/` is empty, so no
  gold-run artifact exists to review. The precondition for building the executor is
  itself unmet (and, per RCA-1, unmeetable on this machine).
- **Why 5 (root):** **The product's designed end state is a governed human-in-the-loop
  organization, not an autonomous one.** Autonomy was correctly deferred; the deferral
  then became permanent because the gate that would lift it cannot be reached.

**Evidence:** grep for `anthropic|openai|subprocess|Popen|dispatch|invoke_agent` over
`**/*.py` → no invocation code · `RUNTIME-AUDIT.md` exclusion list · ledger OS-2
"DEFERRED — needs its own approval" · `runtime/runs/` = README only.

**Root cause:** Intentional scope exclusion whose lifting condition is blocked by RCA-1
— **compounded by a target mismatch**: the docs aim at supervised autonomy, your stated
end state is autonomous operation. Nobody has decided which is being built.
**Contributing:** Empty `runs/` means the gate can't be evaluated; approval-latency
bottleneck (OS-2) already flagged and deferred.
**Consequences:** Every requirement in your end state that depends on execution — tasks,
loops, handoffs, recovery, monitoring — is structurally unreachable. The product is a
control plane with no data plane.
**Corrective:** Two decisions, in order. (a) Fix RCA-1 and record one real gold run.
(b) Explicitly choose the target (DOC-06). If autonomous: build the executor as a
*driver* on top of the existing state machine — poll ready green steps, dispatch to the
owning agent, write the output as evidence, call `complete`. Yellow/red must keep
stopping exactly as they do now.
**Preventive:** Write the autonomy target into `CLAUDE.md` as a stated goal with its own
gates, so "deferred" carries a date and an owner rather than drifting.
**Verify:** A workflow reaches `completed` with no human typing any command for green
steps, and still halts at `awaiting_approval` for the publish step.
**Affects:** AGENT-05, LOOP-02/03, API-07, PLAN-02, WF-08, REC-02, TEST-04, DOC-06.

---

### RCA-3 — Two competing state systems

**Problem:** JSONL runs and SQLite runs both exist; neither is authoritative; approvals
in one are invisible to the other.

- **Why 1:** `company_runtime.py` owns steps/DAG/evidence in files; `db.py` owns
  runs/events/approvals in SQLite. Only `policy.json` is shared.
- **Why 2:** They were built for different increments — OS-6 (controlled runtime) and
  OS-13 (production application foundation) — each satisfying its own acceptance criteria.
- **Why 3:** OS-13 needed authenticated identity and HTTP, which the file runtime could
  not provide; rather than migrate the file runtime behind the service, a second run
  concept was created in SQL.
- **Why 4:** Each increment was validated in isolation. No test asserts that a step
  approved via the API becomes executable in the CLI, because no test spans both.
- **Why 5 (root):** **Increments were scoped by deliverable, not by end-to-end flow, and
  there was no single owner of "the run".** The gap ledger tracks *gaps closed*, not
  *seams created*.

**Evidence:** `Store.create_run()` persists only id/workflow_id/revision/goal/data_class —
no steps · `service.request_approval` requires a `run_id` that need not exist in any
JSONL run · `api.py` has no step routes · no test exercises both paths.

**Root cause:** Deliverable-scoped increments without an integrating end-to-end
acceptance test or a designated owner of run state.
**Contributing:** Ledger optimized for closing rows; two "BUILT" rows (OS-6, OS-13)
independently true while their junction is missing.
**Consequences:** No single answer to "what is ready and who owns it" — the exact query
an autonomous loop must make every tick. Also blocks OBS-04 (run visibility) and UI-05
(approval queue), since neither surface has a complete source.
**Corrective:** Choose one. Recommended: keep the JSONL DAG as the *execution* record
(its hash chain and evidence contract are genuinely good) and make SQLite the *index* —
API reads and projects run state, writes only approvals, which the runtime then consults.
Then add `/v1/runs/{id}/steps` and make `request_approval` reject unknown run/step pairs.
**Preventive:** Add one cross-boundary acceptance test — create run (CLI) → request
approval (API) → decide (API) → step becomes executable (CLI) — and require every future
increment to extend it.
**Verify:** That test passes; `unreconciled`/`awaiting_approval` counts agree across API
and CLI.
**Affects:** ARCH-01, DATA-05, API-02/03/07, OBS-04, UI-05, HITL-05.

---

### RCA-4 — Approvals and escalations are silent

**Problem:** A yellow step parks at `awaiting_approval` and no human is told. A red step
sets `blocked_human` and no human is told. Terminal failures notify nobody.

- **Why 1:** No notification code exists. `send_message` validates that both participants
  are agents on the step or an adjacent DAG edge — a human is not a valid participant.
- **Why 2:** The messaging system was designed for agent↔agent handoffs (OS-9), and the
  human was modelled as the CLI operator who is already present.
- **Why 3:** In a manual-first design that is consistent: the human runs the commands, so
  they see the status output immediately. There is no asynchrony to bridge.
- **Why 4 (root):** **The human-present assumption is exactly what autonomy removes.**
  The moment work advances without a human at the keyboard, "the human will see it" stops
  being true, and every gate becomes an indefinite stall.

**Evidence:** `request_step():189-191` sets the blocked/awaiting states with no side
effect · `send_message` participant check excludes non-agents · no timeout on
`awaiting_approval` · ledger OS-2 names approval latency and defers the fix ·
`playbooks.md` after-hours play acknowledges the problem in prose.
**Root cause:** Human-synchronous design assumption, invalidated by the autonomy target.
**Contributing:** No agent→human channel; no SLA clock on approvals; no approval UI (UI-05).
**Consequences:** Under autonomy, throughput collapses to human polling frequency, and
`blocked_human` runs sit indefinitely. Also breaks REC-06 (dead-letter) and HITL-06.
**Corrective:** Add an outbound notification seam — a pluggable `notify(event, payload)`
called on `awaiting_approval`, `blocked_human`, and every terminal state — plus an
`approval_requested_at` timestamp and an age threshold. Start with the cheapest sink
(append to audit log + a queue file the UI reads); real channels are yellow actions.
**Preventive:** Make "who is told, and by when" a required field in the task contract
(COORD-05).
**Verify:** A yellow step produces a queued notification with a timestamp; an approval
older than threshold surfaces in `/metrics` and the UI.
**Affects:** COORD-06, HITL-02, HITL-04, HITL-06, REC-06, UI-05, OBS-03.

---

### RCA-5 — The audit log has no writer

**Problem:** `logs/audit-log.jsonl` is the governance model's evidentiary backbone, and
nothing in the codebase appends to it.

- **Why 1:** `grep -rn "audit-log.jsonl"` over `.py`, `.sh`, `.ts` (excluding tests)
  returns zero hits. Its 7 lines are hand-authored example data dated 2026-07-14.
- **Why 2:** The `audit-log` skill instructs an *agent* to append by running an ad-hoc
  `python3 - <<'EOF'` heredoc.
- **Why 3:** Logging was designed as a behaviour the model performs, not a side effect
  the system produces — consistent with the repo-wide pattern of governance-as-prompt.
- **Why 4 (root):** **Control and evidence were placed on the same side of the trust
  boundary as the thing being controlled.** A model that forgets, or is induced to skip,
  the append leaves no trace — and the absence is itself invisible.

**Evidence:** no writer in code · `.claude/skills/audit-log/SKILL.md` §"How to append"
prescribes a heredoc · tests assert the *skill text* mentions logging, not that entries
appear · `logs/audit-log.jsonl` unchanged since genesis.
**Root cause:** Self-reported compliance — audit evidence generated by the audited actor.
**Contributing:** No append API; no test asserting a gated action produced a log line;
prose-conformance tests give false assurance.
**Consequences:** No reliable "who approved what, when". Under autonomy this is
disqualifying: the gate could be skipped and nothing would show it. Note the DB already
does this correctly (`operational_events` hash chain in `db.py:186-210`) — the pattern
exists, it just isn't applied here.
**Corrective:** Add `runtime/audit.py` with `append(actor, action, category, target,
approval, evidence, outcome, note)` — validate the nine fields, hash-chain like
`_append_operational_event`, fsync. Call it from `request_step` (yellow/red),
`approve`/`reject`, and every terminal transition. Keep the skill as the read/review
interface.
**Preventive:** Add a test asserting a gated runtime action *produces* a log line — a
behaviour test, replacing the current text-grep.
**Verify:** Running the gold run's publish step appends a valid chained line without any
agent choosing to; tampering breaks verification.
**Affects:** OBS-05, HITL-05, VAL-*, and every governance claim in the ledger.

---

## 8. Recommended First Fix

### The one thing to do first: **make the runtime run on Windows (ARCH-02)**

**Why it comes first**

1. **It gates verification of everything else.** 30 of the erroring tests — the only
   genuine behaviour tests in the repo — fail for this single reason. Until it is fixed,
   no change to orchestration, maker-checker, or messaging can be verified locally, so
   every subsequent P0 would be built blind.
2. **It is the smallest P0.** One new module, two call-site changes. No design decisions,
   no new concepts, no architectural commitment.
3. **It is a strict prerequisite of RCA-2's lifting condition.** The executor is gated on
   "a human reviews a successful gold run"; the gold run cannot be produced here today.
4. **It changes no behaviour.** Same semantics, same states, same tests — the tests
   simply start being able to run. That makes it safe to do before the strategic decision
   in the next paragraph.

**One question I need answered before the *second* step (not this one):**
the documented target is a governed human-in-the-loop company; your stated target is an
autonomous organization. That is DOC-06, and it decides whether we next build the
executor (AGENT-05) or converge state first (ARCH-01). **This first fix is correct under
either answer**, so we can start now and decide after.

### Files likely involved

| File | Change |
|---|---|
| `runtime/filelock.py` | **new** — `exclusive_lock(path)` context manager; `fcntl.flock` on POSIX, `msvcrt.locking` on Windows, single code path for callers |
| `runtime/company_runtime.py` | remove `import fcntl`; `run_lock()` delegates to `exclusive_lock` |
| `scripts/org_state.py` | remove `import fcntl`; `state_lock()` delegates to `exclusive_lock` |
| `requirements.txt` | **new** — pin `pytest` so `tests/test_*.py` can run |
| `.github/workflows/ci.yml` | add `windows-latest` to the `runtime-and-governance` matrix |
| `README.md` | state supported platforms |

### Desired end state

`company_runtime.py` and `org_state.py` import and execute identically on Windows and
Linux, with mutual exclusion preserved on both, and no other behaviour changed.

### Acceptance criteria

1. `python runtime/company_runtime.py validate runtime/workflows/manual-gold-run.json`
   → `workflow valid` on Windows.
2. `python -m pytest tests/ -q` runs (after `pip install -r requirements.txt`).
3. `bash tests/module-controlled-runtime.sh` → **21 passed / 0 failed** on Windows.
4. `bash tests/module-maker-checker.sh` → **18 passed / 0 failed** on Windows.
5. `bash tests/module-organization-management.sh` → **7 passed / 0 failed** on Windows.
6. CI passes on both `ubuntu-24.04` and `windows-latest` — no Linux regression.
7. Concurrency preserved: the existing "concurrent claims serialize to one successor" and
   "concurrent event stream remains valid" tests pass on both platforms.
8. No change to any state name, event name, hash computation, or CLI surface.

### Tests / verification needed

- The three bash suites above, on both platforms (the concurrency cases are the real
  proof — a no-op lock would still pass the sequential tests).
- A new focused test for `filelock.py`: two processes contend for one path; the second
  blocks until the first releases. This is the one thing the current suite does not cover
  directly and the one thing most likely to be silently wrong.
- `git diff` confirming no semantic change outside the two `*_lock` functions.

### Do NOT change yet

- **The JSONL vs SQLite split (ARCH-01)** — that is a real design decision and depends
  on the DOC-06 answer. Do not "helpfully" migrate state here.
- **Anything about execution/dispatch (AGENT-05)** — no executor, no polling, no model
  calls in this change.
- **`policy.json`, state names, event names, hash inputs** — changing any of these
  invalidates the hash chain and every existing acceptance assertion.
- **The 4 failing governance-text tests and the CLAUDE.md size failures** — real, but
  separate commits (SKILL-04, ARCH-04). Keep this diff to locking only.
- **The 116 unresolvable skill references** — needs a dependency decision (ARCH-05), not
  a code fix.

---

## 9. Settled Decisions (2026-09-01) — supersedes the open questions below

The user has ruled on all seven open questions. These are the working source of truth
unless implementation evidence contradicts them. **Do not re-ask them.**

| # | Question | Decision |
|---|---|---|
| DOC-06 | Autonomy model | **Autonomous org with risk-based, exception-only HITL.** Not permanent approval-gating. Ladder: Observe → Recommend → Approval-required → Bounded autonomy → Exception-only escalation. Humans gate irreversible/high-impact acts, spend over a limit, sensitive external comms, destructive ops, permission changes, above-threshold ambiguity, exhausted recovery. Existing HITL docs describe **today**, not the target. |
| SKILL-01 | The 116 referenced skills | **Unverified** until a manifest/registry/installed set/runtime discovery is found. Separate in-repo skills from names in prompts. No registry ⇒ implementation work, not wiring. **Do not create 116 tracker items** — group into reusable skill families first. |
| TEST-06 | Did the gold run ever pass? | **Claimed, not proven.** Do not assert it never passed. Replace the question with the action: reproduce a gold run from current HEAD and persist trigger → execution → role/task transitions → tool & skill invocation → state changes → validation → completion/failure → run record. |
| API-01 / DATA-01 | Was the API/DB ever deployed? | **Historically unknown; currently unproven.** Absent `runtime/data/` proves nothing. Score four tiers separately: unit / local integration / deployed integration / production. Build a reproducible integration environment instead of doing archaeology. |
| UI-01 | Is `apps/control-center` deployed? | **Unknown.** Wrangler config proves intent, not deployment. Score code completeness apart from deployment; builds-and-works-locally is 🟡, not ⛔. |
| HITL-01 | Who approves, and where? | **Role, not a person: Organization Operator/Admin.** Control Center UI is the **canonical approval surface**; Slack/email are notification adapters, never sources of truth. Must define timeout, no-response, rejection, request-for-changes, duplicate approval, approval-after-cancellation, revoked authorization, and restart-while-waiting. |
| ARCH-01 | Tenancy | **Build multi-tenant-safe; operate single-tenant first.** Keep the DB's `org_id` scoping and converge the file runtime onto it. `Organization` becomes a first-class runtime boundary — carry `org_id` explicitly or a context object; never infer a global org. Verify later with cross-org isolation tests. |

**Standing rule.** Convert leftover uncertainty into a verifiable engineering action.
Prefer *"reproduce and persist a gold run"* over *"did one happen?"*; *"instantiate the DB
and run the integration suite"* over *"was it deployed?"*. Distinguish **historical
uncertainty** (evidence unavailable) from **current capability uncertainty** (we don't know
if it works now) — resolve the latter with reproducible tests; the former blocks only when
it changes an architectural decision.

---

## 10. Original Questions (answered above — kept for provenance)


Genuinely undeterminable from the repo:

1. **Target end state (DOC-06).** Docs say human-in-the-loop by design and defer autonomy
   pending human approval; you state autonomy as the goal. Only you can resolve this. It
   determines the entire ordering after the first fix.
2. **Where do the 116 external skills come from?** No manifest declares them. Are they a
   plugin marketplace you have installed, an internal bundle, or aspirational? If
   aspirational, ~90% of role specialization is unimplemented and must enter the tracker
   as build work rather than a wiring gap.
3. **Has the gold run ever actually passed anywhere?** `RUNTIME-AUDIT.md` claims it
   passed locally, but `runtime/runs/` is empty and CI does not commit run artifacts. If
   a passing run exists on another host, its artifact would settle whether the state
   machine is proven or only unit-tested.
4. **Has the API ever been deployed or the DB instantiated?** `runtime/data/` does not
   exist. Every claim about identity, connectors, and reconciliation is unit-level only —
   `❓ Unknown` at integration level.
5. **Is `apps/control-center` deployed?** Wrangler config exists; no deployment evidence.
   Determines whether UI-01 is 🟡 or ⛔ in practice.
6. **Who is the human approver, and through what channel?** Required to design the
   COORD-06/HITL-02 fix. Email? Slack? The UI only?
7. **Single-tenant or multi-tenant?** The DB is `org_id`-scoped throughout; the file
   runtime has no org concept at all. Affects the ARCH-01 convergence design.

---

*Baseline established 2026-09-01 by direct inspection and execution. Commands run:
`bash tests/run.sh` · `python -c "import runtime.company_runtime"` ·
`python -c "import runtime.api"` · `python -m pytest tests/ -q` ·
`grep` sweeps for LLM invocation, schedulers, `fcntl`, and audit-log writers ·
skill-reference resolution script over `.claude/agents/*.md`.*
