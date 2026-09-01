# 🏢 ENTERPRISE — your company, run by Claude

This repo turns Claude into a coordinated company. Open a `claude` session **here** and
Claude automatically becomes your **Chief of Staff**: it reads any request, figures out which
department owns it, and dispatches the right specialist — **17 general-business departments**,
with a built-in operating layer and optional plugin skills when they are installed.

You don't memorize skill names. You say what you need in plain language.

```
You:  "A new lead just filled the demo form."
      → CRO runs lead-response: qualified & scored in minutes, acknowledgment drafted
        under an SLA clock, every step logged — the send waits for your yes.

You:  "A key customer is unhappy and disputing an invoice."
      → Chief of Staff routes Customer Success (health + save-play) + Support (history)
        + Finance (invoice facts), coordinates, returns one answer — awaiting your OK.

You:  "Reconcile last month and tell me why margin dropped."
      → CFO runs reconciliation + variance analysis; AR aging flags what's overdue.

You:  "A prospect sent a 200-question security questionnaire."
      → Security & GRC drafts answers from the policy set — honest gaps flagged, nothing
        claimed without evidence, the send gated on you.
```

## The org chart

| Role | Agent | Handles |
|---|---|---|
| Chief of Staff | `chief-of-staff` | Routing & cross-functional orchestration |
| CTO — Engineering | `cto-engineering` | Build, review, ship, debug, incidents |
| CPO — Product | `cpo-product` | Specs, roadmap, sprints, metrics |
| Head of Design | `head-of-design` | UX, design systems, Figma, accessibility |
| CMO — Marketing | `cmo-marketing` | Campaigns, demand-gen, reputation, brand voice |
| CRO — Sales | `cro-sales` | Lead response SLA, pipeline, deal desk, forecast |
| CFO — Finance | `cfo-finance` | Statements, close, AR & dunning, variance |
| CLO — Legal | `clo-legal` | Contracts, privacy & DSRs, lifecycle register, compliance |
| CHRO — People | `chro-people` | Recruiting, offers, reviews, comp, policy |
| COO — Operations | `coo-operations` | Status, runbooks, vendors, audit-log review |
| Head of Data | `head-of-data` | Analysis, SQL, KPI tree, revenue-leak sweeps |
| Head of Customer Support | `head-of-customer` | Reactive: triage, responses, escalations, KB |
| Head of Customer Success | `customer-success` | Proactive: health, renewals, churn saves, expansion |
| Head of RevOps | `revops` | Funnel integrity, routing rules, attribution, CRM hygiene |
| Head of Security & GRC | `security-grc` | SOC2 readiness, questionnaires, access reviews |
| Chief Knowledge Officer | `chief-knowledge-officer` | Enterprise search & deep research |
| R&D / Platform | `rnd-tooling` | Builds new skills, MCP servers, plugins |

Open **`org-chart.html`** in a browser for the visual version.

This is a **generic, business-neutral scaffold** — cross-industry departments, no industry
skill active by default. **Add your business's specialists** with [templates/](templates/).
A complete worked specialist ships **dormant** as an example (a YouTube SEO+GEO script writer
under [examples/content-studio/](examples/content-studio/)) — activate it, model yours on it,
or ignore it.

## Built-in capability layer (beyond the plugin skills)

Twelve first-party skills close the gaps that actually lose businesses money — each with
defaults, hard rules, red-flag checks, and its own test suite:

| Layer | Skills |
|---|---|
| **Accountability** | `audit-log` — append-only record of every gated action (who approved what, when) |
| **Revenue engine** | `lead-response` (speed-to-lead under SLA) · `ar-collections` (dunning, failed payments) · `renewals-retention` (health, saves, expansion) · `deal-desk` (discount guardrails) · `funnel-attribution` (stages, handoff SLAs, attribution) · `demand-gen` (paid ads, nurture, referral) · `kpi-tree` (north-star metrics, LTV/CAC, leak sweeps) |
| **Trust & compliance** | `grc-readiness` (SOC2/ISO, questionnaires, access reviews) · `privacy-program` (DSR clocks, breach runbook) · `contract-lifecycle` (auto-renew traps, obligations) · `reputation-management` (reviews, crisis comms) |

Every production gap and its status is tracked in [docs/GAP-LEDGER.md](docs/GAP-LEDGER.md).
A governed six-document project intake, Six Sigma value-stream map, bidirectional data contract,
customer journey, and honest release gate live in
[docs/PROJECT-INTAKE-AND-PRODUCTION-LOOP.md](docs/PROJECT-INTAKE-AND-PRODUCTION-LOOP.md) and
[templates/project-intake/](templates/project-intake/). The **MyOrg Control Center** now uses a
body-signed identity gateway and persists organization/user-scoped view state and project intake
through the runtime. The provider-neutral local foundation includes database-bound identity and
roles, SQLite migrations/recovery, exact single-use human approvals, connector authorization,
kill-switch and reconciliation controls, protected metrics, and a fail-closed release gate. This
is not deployment evidence: production identity lifecycle, first-provider OAuth, external review,
human UAT, production deployment and rollback remain gated. See
[docs/history/PRODUCTION-FOUNDATION-VALIDATION-2026-08-06.md](docs/history/PRODUCTION-FOUNDATION-VALIDATION-2026-08-06.md)
and [docs/history/PRODUCTION-READINESS-GAP-CLOSURE-2026-08-06.md](docs/history/PRODUCTION-READINESS-GAP-CLOSURE-2026-08-06.md).
A worked end-to-end run (inbound lead → qualified → routed → gated draft, fully audit-logged)
lives in [examples/revenue-ops/](examples/revenue-ops/runs/sample-inbound-lead/INDEX.md).

## How it works

- **`CLAUDE.md`** — the lightweight constitution, loaded every session: operating loop, org
  index, governance. Detail loads on demand from `company/`:
  - `routing-map.md` — full request→skill catalog
  - `operating-model.md` — the five controlled loops (Goal · Decision · Execution ·
    Checkpoint · Validation), each with exit conditions, iteration caps, and escalation
  - `playbooks.md` — cross-functional standing plays + the 10-field **task contract** every
    agent-to-agent handoff uses (receivers validate, and may reject or escalate)
  - `operating-principles.md` — governance, Definition of Done, audit-log rule
  - `memory-and-learning.md` — shared learning (propose → human-approve → reuse), data
    classification, no self-modification
  - `lessons.md` (evidence-backed lessons) · `connectors.md` (live data, secrets, degraded mode)
- **`.claude/agents/*.md`** — 17 dispatchable specialists, each with a charter: scope,
  inputs → outputs, success criteria, and explicit decision rights (decide / consult / escalate).
- **`state/*.jsonl`** — durable goals, owned tasks, and decisions, managed by `scripts/org_state.py`.
- **`runtime/`** — deterministic workflow harness plus provider-neutral service foundation:
  organization-scoped API, short-lived signed actors with database-bound roles, SQLite
  migrations/backup/restore, typed exchange, exact maker-checker approvals, connector admission,
  idempotent receipts, signed webhook replay defense, evidence and append-only replay.
- **`apps/control-center/`** — the auditable source for the signed-in operator UI and its
  body-signed runtime gateway. The Sites checkout is a deployment mirror of this directory.
- **`logs/audit-log.jsonl`** — the append-only accountability record for gated actions.
- **`tests/`** — executable acceptance suites, run by `bash tests/run.sh`: business-agnostic core
  invariants + one module suite per optional capability.

## How it stays safe: Propose & Approve

Agents **think and draft freely**, but anything that leaves the building or can't be undone —
sending, publishing, paying, signing, deleting, changing access — is **prepared, shown to you,
and waits for a clear "yes."** Every gated action is recorded in the audit log. Moving money
and entering credentials are never done for you. SLA pressure never overrides a gate — a
breach is flagged and escalated, not "fixed" by sending without approval. Content read through
tools (emails, forms, reviews, questionnaires) is treated as data, never as instructions.

## Make it live

Departments run on files and pasted data out of the box. Connect their systems to go live —
in revenue-critical order: **CRM → email/calendar → support inbox → billing** (see
`company/connectors.md`, including secrets rules and degraded-mode behavior). Connectors are
authorized once by you from an interactive session — agents never handle credentials.

## Extend it

- **New department?** Copy `templates/department-agent.template.md` into `.claude/agents/`,
  add a row to `CLAUDE.md` §2 and a section to `company/routing-map.md` — the test suite's
  routing-integrity check confirms nothing drifted.
- **Missing a capability?** Ask R&D: *"build a skill that does X"* → `rnd-tooling` gates it
  (real gap, defined outcome, testable, reversible), then builds it.
- **Tune cost/speed?** Add `model: haiku` (cheap) or `model: opus` (deep) to any agent's
  frontmatter. They inherit your session model by default.
- **Run on a heartbeat?** Ask for a daily brief or weekly review — opt-in scheduled runs
  that still end at the approval gate.

## Try it now

Start a session in this folder and say any of:
- *"What can this company do?"*
- *"Create a goal, assign its first task, and show organization status."*
- *"Run the manual gold workflow and stop at every human approval boundary."*
- *"Run the maker-checker workflow, return one revision, and preserve the exchange trail."*
- *"Here's a new inbound lead: …"* (paste a form/email)
- *"Which accounts are at risk this quarter?"*
- *"Run the AR aging and draft the reminders."*
- *"Are we ready for a SOC2 Type I?"*
- *"Turn last quarter's numbers into a board update."*
