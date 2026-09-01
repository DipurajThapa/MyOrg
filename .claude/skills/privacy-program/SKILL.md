---
name: privacy-program
description: >
  Handle personal data lawfully and provably: DSR (data-subject request) workflow with
  statutory clocks, consent and lawful-basis mapping, data-retention schedule, breach-
  notification runbook (72h GDPR clock), and privacy-review of new processing. Owned by
  clo-legal with security-grc. Use when a DSR arrives, a breach is suspected, a new tool
  processes personal data, or retention/consent needs defining. Assists compliance — it is
  not legal counsel.
---

# Privacy Program — DSRs answered on time, breaches handled by the clock

## When to use
A DSR lands (access/deletion/correction/portability), a breach is suspected, a new
tool/vendor/feature processes personal data, or consent/retention questions arise.

## 1. DSR workflow (the statutory clock starts at receipt)

| Step | Action | Clock discipline |
|---|---|---|
| 1 | Log receipt via `audit-log` (`dsr.received`, target = request ID, **no PII**) | GDPR: 1 month · CCPA: 45 days — deadline computed and stated at intake |
| 2 | Verify the requester's identity (route the method past the human — identity checks touch credentials) | before any data moves |
| 3 | Scope: which systems hold this person's data (data map / `enterprise-search`) | named list, not "probably" |
| 4 | Compile / delete / correct as requested — **deletion is destructive → prepared as a checklist the human executes (🔴 for hard-deletes)** | evidence per system |
| 5 | Draft the response — **sending is 🟡**, logged | before deadline; extensions (allowed once under GDPR) are a human decision |
| 6 | Close: outcome + dates logged | the log is the compliance evidence |

## 2. Breach-notification runbook (72 hours is short)
1. Suspicion → log immediately (`breach.suspected`), freeze relevant evidence, start the
   internal clock. (The statutory GDPR 72-hour clock runs from **awareness** — when the breach
   is reasonably established — starting internally at suspicion ensures awareness is never late.)
2. Assess with `security-grc` + `cto-engineering`: what data, whose, how much, ongoing?
3. Severity: does it reach the notify threshold (risk to individuals)? Document the reasoning
   **either way** — "we assessed and didn't notify because X" must be defensible.
4. Draft notifications (authority within 72h under GDPR; individuals if high risk) — **all
   sends are 🟡, human-approved**; recommend outside counsel for anything material.
5. Post-incident: cause → `company/lessons.md` proposal; control gaps → `grc-readiness`.

## 3. Consent & lawful basis
Map each processing purpose → lawful basis (consent, contract, legitimate interest…) →
where consent is captured → how withdrawal works. Marketing sends require a basis check
(`demand-gen`/`cmo-marketing` consult before list use).

## 4. Retention schedule
Per data category: keep-for (with the reason: statutory, contractual, operational) →
then delete/anonymize. Deletion runs are prepared as checklists; the human executes (🔴).
Aligns with `company/memory-and-learning.md` (no PII in memory/logs).

## 5. Privacy review of new processing
New tool/feature/vendor touching personal data → quick DPIA-style pass: purpose, data
categories, basis, flows (cross-border?), retention, subprocessors → verdict + conditions;
DPA routing to `clo-legal` contract work.

## Hard rules
- **Clocks are computed at intake and stated in every status** — a missed DSR deadline is a
  reportable failure, not an oops.
- **This skill prepares; it does not opine.** Frame outputs as information; material risk →
  recommend counsel (per `clo-legal` governance).
- No PII in the audit log, task contracts, or URLs — request IDs only.
- Identity verification before disclosure — wrong-person disclosure is itself a breach.

## Red flags
- *"It's probably not a reportable breach."* → Assess it properly and write the reasoning down; "probably" defends nothing.
- *"Delete it everywhere now."* → Hard-deletes are 🔴 — checklist for the human, system by system.
- *"The deadline is far off."* → Scoping always takes longer than expected; the clock statement goes in today.

## Verification before claiming done
Every open DSR shows: deadline, systems scoped, evidence per step, response state (`pending`
until human-approved). Breach records show the clock, the assessment reasoning, and who decided.
