# Cross-functional playbooks

**Load this when** a request spans departments and the Chief of Staff needs to sequence the work.
Decompose the request, define "done" for each leg before dispatching it (see
`operating-principles.md` §7), route each leg to the owning department, and gate the outward steps.

## Standing plays

- **Ship a feature** → CPO (`write-spec`) → Design (`design-critique`) → CTO (`architecture` → build → `code-review` → `deploy-checklist`) → CMO (`campaign-plan`) → CRO (`create-an-asset`) → Customer (`kb-article`).
- **Board / investor update** → Head of Data (metrics) → CFO (`financial-statements`, `variance-analysis`) → CPO (`stakeholder-update`) → CMO (narrative) → assemble via `anthropic-skills:pptx`.
- **New vendor** → security-grc (`grc-readiness` vendor security review — required before any data access) → CLO (`vendor-check`, `review-contract`) → COO (`vendor-review`) → CFO (spend) → sign via `legal:signature-request` *(approval-gated)*.
- **Customer churn risk** → **Customer Success leads** (`renewals-retention`: health score → cause → save-play) → head-of-customer (support history) → CRO (commercials) → CPO (product gaps with revenue-at-risk) → `customer-escalation` if needed.
- **Launch review / go-to-market** → CMO + CRO + Product + Legal (`compliance-check`) in parallel, you synthesize.
- **Incident** → CTO (`incident-response`) leads → COO (`status-report`) → Customer (direct-to-affected-customer comms draft) → **public statements via CMO's `reputation-management` C-levels** → CLO if data/regulatory exposure; security events add security-grc.
- **Inbound lead** → CRO (`lead-response`: qualify → route → gated draft under SLA) → `deal-desk` when a quote forms → closed-won hands to Customer Success (onboarding) via task contract.
- **Overdue invoice / failed payment** → CFO (`ar-collections`: cause first, then the gated dunning ladder) → disputes route to the owning dept → 60+ days = human decision (write-off / agency / CLO).
- **Renewal window (T-90)** → Customer Success (`renewals-retention`: health → play) → CRO on commercials → `deal-desk` for terms → CPO gets product-gap contracts with revenue-at-risk.
- **DSR received** → CLO (`privacy-program`: clock stated at intake) → systems scoped with security-grc/CKO → deletion checklists to the human (🔴) → gated response before deadline.
- **Suspected breach** → security-grc + CTO assess (72h clock) → CLO/`privacy-program` on notification duty → `reputation-management` C1 comms — every outward statement human-approved.
- **Public negative review / thread** → CMO (`reputation-management` triage) → underlying issue routed to owner dept as a task contract → gated reply → recurring themes to CPO.
- **After-hours coverage** — no unattended sends, ever: HOT-lead drafts prepared if a session is active; otherwise SLA clocks pause per policy and the human is notified at next session. Optional scheduled runs (§5 cadences) can pre-draft; they still end at the 🟡 gate.

## Handoffs between departments — the Task Contract

Agents hand off through a **task contract**, not raw context. Every contract carries:

| Field | What it states |
|---|---|
| **Objective** | the outcome this leg must produce |
| **Context** | only what the receiver needs — no transcripts, no unrelated history |
| **Inputs & sources** | the data/files and where they live |
| **Constraints** | limits, standards, non-goals |
| **Expected output** | the concrete deliverable |
| **Acceptance criteria** | how "done" is verified (`operating-principles.md` §7) |
| **Decision authority** | what the receiver may decide vs. must escalate |
| **Risks & assumptions** | including assumptions still needing validation |
| **Deadline / checkpoint** | when progress is reviewed (`operating-model.md` §4) |
| **Escalation condition** | what triggers handing it back or up |

**The receiver validates the contract before executing.** It may **reject, return, or escalate**
work that is incomplete, contradictory, unsafe, duplicated, or outside its scope — stating why.
Keep each leg's working set minimal. Use peer review only where it adds measurable value; don't
spin up two agents for one role.

For multi-step or cross-session work, encode each accepted contract as a workflow step using
`runtime/workflows/manual-gold-run.json` as the schema. The runtime, not the model, decides when a
dependency is ready, a retry is available, approval is required, or the workflow must stop.

Where an internal artifact drives a downstream decision, name a checker different from the maker.
Exchange only typed artifact references through the runtime. Checker approval releases the next
leg; checker return reopens the maker leg within its review cap. This quality gate never replaces
human approval for outward, costly, security-sensitive, or irreversible actions.

## Parallel fan-out

For true parallel work across many items (audits, migrations, large reviews), the **Workflow tool**
can orchestrate a fleet of agents deterministically — but only when the user explicitly asks for a
workflow/swarm run (it can spend a lot of tokens).
