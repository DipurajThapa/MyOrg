# Lead-Response SLA Policy — EXAMPLE (filled for a fictional B2B SaaS)

A worked example of a dedicated-mode policy, tuned for "Northstar Metrics" — a fictional
B2B analytics SaaS selling to 50–500-person companies. Shows how targets and ICP get tuned;
copy the shape, not the values.

## Response-time targets (clock starts at intake receipt)

| Band | Score | Target: draft ready + human notified |
|---|---|---|
| **HOT** | 4–5 | ≤ 10 minutes |
| **WARM** | 2–3 | ≤ 2 business hours |
| **COLD** | 0–1 | ≤ 1 business day |

**Business hours:** Mon–Fri, 08:00–17:00 US Eastern. Out-of-hours HOT leads: draft immediately
if a session is active; otherwise clock pauses to next business hour, pause noted in the log.

## Scoring thresholds

Per `references/qualification-rubric.md`. ICP for Northstar: B2B software or e-commerce company,
50–500 employees, US/EU, with an analytics or reporting pain named in the inquiry.

## Routing

| Condition | Owner |
|---|---|
| Default — all qualified leads | `cro-sales` |
| Existing customer asking for help | `head-of-customer` |
| Partnership / press / vendor pitch | `chief-of-staff` (classify, then route) |
| Enterprise (>500 employees) inbound | `cro-sales`, flag for founder review |

## Breach handling

Log `sla.breach` (`outcome: breach-flagged`) with minutes elapsed → escalate immediately →
≥ 2 breaches/week proposes a lesson. **Never respond without approval to "fix" a breach.**

## Sending

Every send is 🟡 gated per `company/operating-principles.md` §1. No exceptions, including breaches.
