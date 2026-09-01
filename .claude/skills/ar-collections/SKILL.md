---
name: ar-collections
description: >
  Stop revenue leakage from unpaid invoices and failed payments: AR aging analysis, a staged
  dunning ladder (all sends approval-gated), failed-payment (involuntary-churn) recovery, and
  collections escalation. Owned by cfo-finance. Use when invoices are overdue, cards fail,
  or you need an AR health readout. Works from exported/pasted AR data until a billing
  connector (Stripe/QuickBooks) is authorized.
---

# AR & Collections — the revenue companies silently lose to unpaid invoices

## When to use
Overdue invoices, failed/expired card payments, monthly AR review, or before close
(`finance:close-management`) to explain AR movements.

## The pipeline

1. **Age the book.** Bucket every open invoice: current · 1–30 · 31–60 · 61–90 · 90+ days.
   Compute totals, count, and % of ARR per bucket. Flag anything 90+ individually.
2. **Classify the cause** per overdue item — failed payment (card expired/declined), disputed,
   ghosting, terms mismatch, or our own invoicing error. **The cause picks the play; never dun
   a customer whose invoice we got wrong.**
3. **Run the dunning ladder** (defaults below — tune per business). Every send is a **DRAFT,
   🟡 approval-gated**, logged via `audit-log` (`action: dunning.send`, `approval: pending`).
4. **Failed-payment recovery** (involuntary churn): detect expiring/declined cards → draft a
   card-update request (friendly, not a dunning letter) → gated send → track recovery rate.
5. **Escalate** at the ladder's end: prepare the case file (invoice trail, comms log, amount)
   and recommend — human decides between write-off (CFO), collections agency, or legal (CLO).
6. **Report**: DSO, aging trend, recovery rate, and the top-5 exposures with next actions.

## Default dunning ladder (tune in place; per-step gated)

| Step | When | Tone | Channel |
|---|---|---|---|
| 1 | 3 days before due | friendly reminder + invoice link | email |
| 2 | 1 day overdue | polite nudge, assume oversight | email |
| 3 | 7 days overdue | direct: amount, due date, ask for a date | email |
| 4 | 21 days overdue | firm: account impact, offer payment plan | email + owner call prep |
| 5 | 45 days overdue | final notice before escalation | email, CC leadership |
| 6 | 60+ days | escalate: agency / legal / write-off — human decision | case file |

## Hard rules
- **Every send is drafted and approval-gated** — recipient + copy shown first. No exceptions.
- **You never move money** — no charging cards, no refunds, no payment plans committed without
  approval (payment terms are contract changes → CLO/CFO consult).
- **Verify before dunning:** invoice correct, actually sent, payment not already received.
  Dunning a paid customer costs more goodwill than the invoice is worth.
- Log every ladder step via `audit-log`; disputed invoices pause the ladder and open a task
  contract to the owning department.
- Account numbers and payment details never appear in drafts, logs, or URLs.

## Red flags — stop if you catch yourself thinking
- *"Skip to a firmer step — they're very late."* → The ladder exists to protect the relationship; escalate through it, don't jump it.
- *"Auto-send the reminders, they're low-risk."* → Every send is 🟡. A wrong reminder to a paid customer is outward damage.
- *"Round the aging buckets from memory."* → Compute from the actual data; state the as-of date.

## Verification before claiming done
Aging totals reconcile to the AR balance; every overdue item has a cause + next step; every
draft is logged `pending`; the report states data as-of date and what's excluded.
