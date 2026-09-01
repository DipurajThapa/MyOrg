---
name: renewals-retention
description: >
  Protect and grow existing revenue: account health scoring, a renewal pipeline worked
  90 days out, churn-risk save-plays, expansion plays, and CSAT/NPS tracking. Owned by
  customer-success. Use for renewal forecasting, at-risk accounts, onboarding-to-value
  plans, QBR prep, and churn post-mortems. Works from exported usage/support/contract
  data until connectors are live.
---

# Renewals & Retention — revenue you already earned, kept

## When to use
A renewal inside 90 days, usage/engagement drops, a churn threat, expansion planning,
CSAT/NPS review, or a churn post-mortem.

## Health score (evidence-based, 0–10)

| Dimension | 0–2 pts each | Evidence source |
|---|---|---|
| Product usage | trend vs. their baseline (not vs. other accounts) | usage data |
| Engagement | exec sponsor active? champions present? QBR attended? | comms/CRM |
| Support load | volume + severity trend; unresolved P1/P2s | tickets |
| Value realization | are they hitting the outcome they bought for? | onboarding goals, QBR notes |
| Commercial | payment on time, no downgrade signals, multi-year? | AR + contract |

**8–10 healthy → expansion play · 4–7 watch → engagement play · 0–3 at-risk → save-play.**
Unknown dimensions score 0 and are named as data gaps — never guessed.

## The renewal pipeline (work it backwards)

| Checkpoint | Action |
|---|---|
| T-90 days | health score; classify healthy/watch/at-risk; open the renewal record |
| T-60 | at-risk → save-play running; healthy → expansion hypothesis + QBR scheduled |
| T-30 | commercial proposal drafted (gated); blockers escalated to human |
| T-14 | unsigned → escalate to human with state + recommendation |
| T-0 | renewed / churned → log outcome; churned → post-mortem → propose a lesson |

## Save-plays (pick by cause, not by panic)
- **Value gap** → re-onboarding sprint to first-value; success plan with dates.
- **Champion left** → multi-thread: map new stakeholders, draft exec-to-exec note (gated).
- **Product gap** → task contract to CPO with revenue-at-risk quantified; honest timeline to customer (no promises Engineering hasn't confirmed).
- **Price objection** → value recap first; any discount/term change is CFO/CLO + human approval.
- **Support pain** → joint review with head-of-customer; unresolved-ticket burn-down.

## Hard rules
- **Every customer-facing send, discount, credit, or term change is 🟡** — drafted, shown, approved. Log via `audit-log` (`renewal.checkpoint`, `save.play.opened`, `message.send` pending).
- Renewal forecasts state assumptions and a range; "verbal yes" is not "renewed."
- Churn post-mortems record the *validated* cause and propose a `company/lessons.md` entry.
- CSAT/NPS: report the number, the trend, response rate, and top detractor themes — never cherry-pick.

## Red flags
- *"Usage is fine, skip the health check."* → Usage is one of five dimensions; healthy-looking accounts churn on sponsor loss.
- *"Offer 20% off to make it easy."* → Discounts are the last play, human-approved, after value recap.
- *"Mark it renewed — they said they would."* → Renewed = signed. Until then it's forecast.

## Verification before claiming done
Every account in the 90-day window has a scored health record and a classified play; at-risk
accounts have owner + date; the forecast reconciles to the contract list; gated actions logged.
