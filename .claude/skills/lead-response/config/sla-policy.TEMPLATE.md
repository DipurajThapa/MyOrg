# Lead-Response SLA Policy — TEMPLATE

Copy over `sla-policy.md` and fill in every `<UNSET>` to run the skill in **dedicated mode**.
While any `<UNSET>` remains, the skill runs in general mode using the EXAMPLE as a reference shape.

## Response-time targets (clock starts at intake receipt)

| Band | Score | Target: draft ready + human notified |
|---|---|---|
| **HOT** | 4–5 | <UNSET — e.g. ≤ 15 minutes> |
| **WARM** | 2–3 | <UNSET — e.g. ≤ 4 business hours> |
| **COLD** | 0–1 | <UNSET — e.g. ≤ 24 business hours> |

**Business hours:** <UNSET — days + hours + timezone; and the out-of-hours rule for HOT leads>

## Scoring thresholds

Per `references/qualification-rubric.md`: ICP fit (0–2) + intent/urgency (0–2) +
completeness (0–1). HOT ≥ 4 · WARM 2–3 · COLD ≤ 1.
ICP definition for this business: <UNSET — who is a fit: segment, size, geography, use case>

## Routing

| Condition | Owner |
|---|---|
| Default — all qualified leads | <UNSET — usually `cro-sales`> |
| Existing customer asking for help | <UNSET — usually `head-of-customer`> |
| Partnership / press / vendor pitch | <UNSET — usually `chief-of-staff`> |

## Breach handling

1. Log `sla.breach` with `outcome: breach-flagged` and the minutes elapsed.
2. Escalate to the human immediately with the ready-or-blocked state of the draft.
3. Recurring breaches (≥ <UNSET — e.g. 2> in a week) → propose a `company/lessons.md` entry.
**Never respond without approval to "fix" a breach.**

## Sending

Every send is 🟡 gated per `company/operating-principles.md` §1. No exceptions, including breaches.
