# Task Contract — lead-2026-07-14-001 → `cro-sales`

Handoff per `company/playbooks.md` (the Task Contract). Receiver validates before executing and
may reject, return, or escalate.

| Field | Value |
|---|---|
| **Objective** | Convert qualified HOT inbound lead-2026-07-14-001 into a booked demo this week |
| **Context** | 200-person robotics company; support ticket volume doubled; wants triage without engineers; budget approved; asked for a demo this week |
| **Inputs & sources** | `00-intake.md` (raw submission) · `01-qualification.md` (5/5 HOT) · `02-acknowledgment-DRAFT.md` (awaiting send approval) |
| **Constraints** | No pricing/discount/contract commitments in early conversation without CFO/CLO input; no fabricated availability or claims |
| **Expected output** | Approved acknowledgment sent (post-approval) → demo scheduled → call-prep brief (`sales:call-prep`) before the demo |
| **Acceptance criteria** | Demo on the calendar with a named attendee, OR a documented decision that the lead is not pursued — either way, lifecycle logged in `logs/audit-log.jsonl` |
| **Decision authority** | *Decide:* messaging, sequencing, demo agenda. *Consult:* CFO/CLO on pricing/terms. *Escalate:* every send (🟡 gate); any commitment |
| **Risks & assumptions** | Assumes stated budget approval is real (unvalidated — flagged); "this week" timeline expires fast, so approval latency is the main risk |
| **Deadline / checkpoint** | Send-approval requested now; if no human decision by 2026-07-15T09:12Z, re-surface once (checkpoint), then hold |
| **Escalation condition** | SLA breach on any follow-up; lead requests pricing/terms; lead goes silent ≥ 5 business days after send |

**Audit:** routing decided at 09:16:10Z (`lead.routed`); this contract handed off at
09:19:05Z (`lead.handoff`).
