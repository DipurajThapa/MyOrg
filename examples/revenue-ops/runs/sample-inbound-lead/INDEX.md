# Run Manifest — sample-inbound-lead (lead-2026-07-14-001)

The worked example for the `lead-response` skill: one fictional inbound lead taken from intake
to a gated, SLA-met acknowledgment draft. All person/company data is fictional
(`.example` domain). Validated by `tests/module-lead-response.sh`.

| # | Artifact | What it shows |
|---|---|---|
| 00 | `00-intake.md` | Raw capture, ID assigned, SLA clock started (09:12:04Z) |
| 01 | `01-qualification.md` | Rubric scoring: 5/5 → HOT → 15-minute SLA target |
| 02 | `02-acknowledgment-DRAFT.md` | Personalized draft, ready at +6m43s (SLA met ✅), **not sent** — awaiting approval |
| 03 | `03-task-contract-to-cro.md` | 10-field task contract handing ownership to `cro-sales` |

## Audit trail (in `logs/audit-log.jsonl`)

| ts (UTC) | action | approval | outcome |
|---|---|---|---|
| 09:12:04 | `lead.intake` (SLA clock start) | not-required | ok |
| 09:15:32 | `lead.qualified` | not-required | ok |
| 09:16:10 | `lead.routed` | not-required | ok |
| 09:18:47 | `lead.response.drafted` | not-required | ok |
| 09:18:52 | `email.send` | **pending** | **awaiting-approval** |
| 09:19:05 | `lead.handoff` (task contract) | not-required | ok |

The run ends exactly where the governance model says it must: everything prepared, evidence
logged, **send gated on the human**. The `email.send` entry stays `pending` in this example —
the correct terminal state for a scaffold demo (no real recipient exists). A live decision would
**append** a new `email.send` line (`granted`/`denied`) — the pending line is never edited.
