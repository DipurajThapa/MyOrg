# Documentation index

## Start here

| Document | What it is |
|---|---|
| [AUTONOMY-AUDIT-2026-09-01-REV2.md](AUTONOMY-AUDIT-2026-09-01-REV2.md) | **The source of truth.** What is built, what is not, the tracker with stable IDs, the root-cause analyses, and the investigation ledger. Every implementation cycle updates it. |
| [TARGET-STACK-MIGRATION-ASSESSMENT.md](TARGET-STACK-MIGRATION-ASSESSMENT.md) | The intended stack (Hostinger VPS, Docker Compose, FastAPI, Postgres, Redis, Vite) measured against what exists, with effort and sequencing. |

## How to run and operate it

| Document | What it is |
|---|---|
| [OPERATIONS-RUNBOOK.md](OPERATIONS-RUNBOOK.md) | Day-to-day operation: starting the service, backups, maintenance, what to do when something stops. |
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
