---
name: deal-desk
description: >
  Protect margin on every quote: pricing/discount guardrails, a discount approval matrix,
  non-standard-terms review routing, and quote assembly. Owned by cro-sales with cfo-finance;
  clo-legal on terms. Use when building a quote/proposal, when a discount is requested, or
  when a deal has non-standard terms. Prevents uncontrolled discounting — the classic silent
  margin leak.
---

# Deal Desk — every discount is a decision, not a reflex

## When to use
Any quote/proposal, any discount request, any non-standard term (payment terms, SLA promises,
custom clauses, unusual renewal/termination rights).

## The pipeline

1. **Assemble the quote** from the standard price book. State list price first — every
   deviation is measured from list, visibly.
2. **Apply the discount matrix** (defaults below). The requested discount picks the approval
   lane; the quote shows list, discount %, net, and margin impact.
3. **Screen the terms.** Anything non-standard → route: commercial terms → CFO; legal clauses
   → CLO (`legal:review-contract`); SLA/support promises → confirm with CTO/Customer first.
4. **Package the approval**: one summary — customer, ACV, discount, term deviations, margin,
   precedent risk ("if we give X this, Y will ask") — then the 🟡 gate.
5. **Gate the commitment.** A discount is money the company gives away, so it goes through
   the runtime rather than into a note -- and the runtime writes the record, not you
   (`CLAUDE.md` §3):

   ```bash
   python -m runtime.company_runtime gate <quote-id>      --owner cro-sales --action commit_spend      --summary "<customer, list price, discount %, term, who asked>"      --request-id <quote-id>
   ```

   It prints `awaiting_approval`. The quote is a draft until a named human approves it in
   the console.
6. **Record the outcome** — approved discounts become precedent data; track discount trend
   quarterly (creeping average discount = the leak indicator).

## Default discount matrix (tune to your business; the lanes are the point)

| Discount | Approval needed |
|---|---|
| 0% – 10% | deal owner (still logged) |
| >10% – 20% | + CFO review |
| >20% – 30% | + human approval, written justification |
| >30% | human only — treat as a pricing decision, not a discount |
| Any non-standard term | CLO/CFO review regardless of discount |

## Hard rules
- **No quote, discount, or term commitment reaches a customer without explicit human approval** — the matrix routes *internal review*; the send is always 🟡.
- **Never invent pricing** — no price book entry, no quote; escalate for a pricing decision.
- Multi-year, prepay, or case-study trades must be *priced* trades ("X% for Y commitment"), stated in the approval package.
- Discounts expire: every discounted quote carries a validity date.
- Margin floors: quotes below the floor (default: 60% gross margin) are flagged in red in the approval package.

## Red flags
- *"It's quarter-end, just match their ask."* → Quarter-end discounts are how the average creeps; the matrix applies especially now.
- *"The competitor is cheaper, we have to."* → Competitive pressure goes in the justification field, human decides.
- *"Small custom clause, no need for Legal."* → Non-standard terms outlive the deal; CLO sees them all.

## Verification before claiming done
The quote math checks (list → discount → net → margin); every deviation has its lane's review;
the approval package is complete; the lifecycle is logged; nothing was sent.
