# Lead-Response SLA Policy — ACTIVE

The operating policy for this business. Filled in → the skill runs in **dedicated mode** and
follows it exactly. To reset to general mode, restore from `sla-policy.TEMPLATE.md`.

## Response-time targets (clock starts at intake receipt)

| Band | Score | Target: draft ready + human notified |
|---|---|---|
| **HOT** | 4–5 | ≤ 15 minutes |
| **WARM** | 2–3 | ≤ 4 business hours |
| **COLD** | 0–1 | ≤ 24 business hours |

**Business hours:** Mon–Fri, 09:00–18:00 **UTC** *(edit to your business timezone — deadlines
are computed against this zone)*. Outside business hours, HOT leads are drafted immediately if
a session is active; otherwise the clock pauses until the next business hour and the pause is
noted in the log entry.

## Scoring thresholds & ICP

Per `references/qualification-rubric.md`: ICP fit (0–2) + intent/urgency (0–2) +
completeness (0–1). HOT ≥ 4 · WARM 2–3 · COLD ≤ 1.

**ICP definition for this business** *(the rubric's dimension 1 scores against this — edit to
yours)*: a B2B company, 20–1,000 employees, in a served geography, with a named operational
pain our product addresses. Clearly outside any of these → ICP 0; partial/unknown → ICP 1.

## Routing

| Condition | Owner |
|---|---|
| Default — all qualified leads | `cro-sales` |
| Existing customer asking for help (not a new deal) | `head-of-customer` |
| Partnership / press / vendor pitch | `chief-of-staff` (classify, then route) |

## Breach handling

1. Log `sla.breach` with `outcome: breach-flagged` and the minutes elapsed.
2. Escalate to the human immediately with the ready-or-blocked state of the draft.
3. Recurring breaches (≥ 2 in a week) → propose a `company/lessons.md` entry with the cause.
**Never respond without approval to "fix" a breach.**

## Sending

Every send is 🟡 gated: the human sees the recipient, the copy, and says yes — per
`company/operating-principles.md` §1. No exceptions, including breaches.
