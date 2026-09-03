# Documentation index

## Start here

| Document | What it is |
|---|---|
| [EXECUTION-TRACKER.md](EXECUTION-TRACKER.md) | **The source of truth for status.** One tracker for all open work (`A-*`, `B-*`, REV2 ids), the decisions still owed, and the specs for what is being built. Updated in place. |
| [AUTONOMY-AUDIT-2026-09-01-REV2.md](AUTONOMY-AUDIT-2026-09-01-REV2.md) | What is built and what is not, with the root-cause analyses and the investigation ledger. Its status columns now defer to the tracker. |
| [TARGET-STACK-MIGRATION-ASSESSMENT.md](TARGET-STACK-MIGRATION-ASSESSMENT.md) | The intended stack (Hostinger VPS, Docker Compose, FastAPI, Postgres, Redis, Vite) measured against what exists, with effort and sequencing. |

## How to run and operate it

| Document | What it is |
|---|---|
| [OPERATIONS-RUNBOOK.md](OPERATIONS-RUNBOOK.md) | Day-to-day operation: starting the service, backups, maintenance, what to do when something stops. |
| [TRIGGERS-AND-LIVE-CONNECTORS.md](TRIGGERS-AND-LIVE-CONNECTORS.md) | Running the loop as a service, registering schedules and signed webhooks, and reaching a real external system — including what to do when a call left and never came back. |
| [ARCHITECTURE-OPPORTUNITIES-2026-09-01.md](ARCHITECTURE-OPPORTUNITIES-2026-09-01.md), [-2026-09-02.md](ARCHITECTURE-OPPORTUNITIES-2026-09-02.md) | The two review cycles of the control architecture: which harnesses, loops and hooks are worth building, which are not, and why. Reasoning records; status lives in the tracker. |
| [UI-DESIGN-DIRECTION.md](UI-DESIGN-DIRECTION.md) | The Control Center design system, and which parts of the supplied dashboard reference to take, adapt, and ignore. |
| [UAT-DEPLOYMENT-AND-ROLLBACK.md](UAT-DEPLOYMENT-AND-ROLLBACK.md) | The deployment and rollback drill. Written, not yet executed. |
| [PROJECT-INTAKE-AND-PRODUCTION-LOOP.md](PROJECT-INTAKE-AND-PRODUCTION-LOOP.md) | The governed six-document intake and the value-stream loop the Control Center's intake screen implements. |

## Design and control

| Document | What it is |
|---|---|
| [SECURITY-THREAT-MODEL.md](SECURITY-THREAT-MODEL.md) | Trust boundaries, threats, and the controls that answer them. |
| [EXCHANGE-MAKER-CHECKER-AUDIT.md](EXCHANGE-MAKER-CHECKER-AUDIT.md) | The typed agent-to-agent exchange and the maker-checker rules. Checked by the acceptance suite. |
| [GAP-LEDGER.md](GAP-LEDGER.md) | The production-readiness gap list as of 2026-08-06. Superseded for *status* by the REV2 audit; kept because the suite checks its dispositions. |
| [RUNTIME-AUDIT.md](RUNTIME-AUDIT.md) | The runtime's original scope and deliberate exclusions. Superseded for *scope* — the executor, planner, scheduler and grading have since landed. |

## History

[`history/`](history/) holds superseded reports, kept as evidence rather than deleted: the
first autonomy baseline (REV1, replaced the same day by REV2) and the 2026-08-06
production-foundation, gap-closure, validation and security-review reports. Read them for
what was true then, never for what is true now.

---

*Anything not listed here is not current. If a document and the code disagree, the code
wins and the document is wrong — say so and fix it rather than making the code match.*
