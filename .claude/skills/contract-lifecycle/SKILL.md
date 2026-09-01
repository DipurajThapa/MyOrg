---
name: contract-lifecycle
description: >
  Stop contracts from leaking money after signature: an obligations register (what we owe,
  what they owe), a renewal/expiry calendar with auto-renew-trap detection, termination-window
  tracking, and price-escalation monitoring. Owned by clo-legal with coo-operations. Use at
  contract signature (to register it), monthly (calendar sweep), or before any renewal/
  termination decision. Complements legal:review-contract (pre-signature) — this is
  post-signature.
---

# Contract Lifecycle — the money contracts leak *after* signing

## When to use
A contract is signed (register it) · monthly calendar sweep · a renewal/termination decision
approaches · "what are we actually committed to?"

## 1. The obligations register (one row per obligation)
For every active contract capture: counterparty · start/end dates · **auto-renew? notice
window?** · our obligations (deliverables, SLAs, payment terms) · their obligations · price +
escalation clauses · termination rights · owner department. Source of truth is the contract
text — quote the clause, cite the section.

## 2. The renewal/expiry calendar (worked backwards)
| Checkpoint | Action |
|---|---|
| T-120 days | flag upcoming renewals/expiries to the owner department |
| T-90 | decision package: usage/value vs. cost, market alternative check (`operations:vendor-review`) |
| T-(notice window + 14) | **auto-renew trap alert**: "decide now or it renews itself" — escalate to human |
| T-0 | outcome logged; terms changes → re-register |

**The auto-renew trap is the #1 leak this skill exists for:** a contract that renews unless
cancelled N days early, noticed N-1 days early. The calendar fires *before* the notice window
closes, always.

## 3. Escalation & price-change monitoring
Flag: CPI/percentage escalators about to trigger · usage tiers about to step · payment terms
we're violating (late fees) · SLA credits we're owed but not claiming.

## Hard rules
- Register from the **signed** document only; drafts don't create obligations.
- Terminating, renewing, renegotiating, or letting an auto-renewal pass are **human decisions
  (🟡)** — prepare the decision package, log via `audit-log` (`contract.renewal.decision`,
  `approval: pending`).
- Missed-window events are logged (`sla.breach`-style: `contract.window.missed`) and proposed
  as a `company/lessons.md` entry — a missed notice window should never repeat.
- This is information, not legal advice — material interpretation → outside counsel
  (per `clo-legal` governance).

## Red flags
- *"It'll probably just renew, no action needed."* → That sentence costs a year of unwanted spend. Decide before the window.
- *"The obligations are roughly what the summary said."* → Quote the clause. Summaries drift; clauses bind.
- *"We can register it later."* → Unregistered contracts are invisible leaks. Register at signature.

## Verification before claiming done
Every active contract has a register row with dates + notice window quoted from the text;
the calendar has no contract inside its notice window without a logged human decision;
sweep output lists what was checked and the as-of date.
