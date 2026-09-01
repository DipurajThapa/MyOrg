---
name: funnel-attribution
description: >
  Make the revenue funnel measurable and leak-proof: canonical stage definitions, MQL→SQL
  handoff SLAs, lead-routing rules, marketing attribution, and CRM hygiene audits. Owned by
  revops. Use when Marketing and Sales disagree on numbers, leads leak between teams, channel
  ROI is unknown, or the CRM is dirty. Works from exported funnel data until a CRM connector
  is live.
---

# Funnel & Attribution — one funnel, one set of numbers

## When to use
Funnel-number disputes, leads going stale between teams, "which channel pays?" questions,
CRM hygiene checks, or designing/tuning stage definitions and handoff SLAs.

## The five jobs

### 1. Canonical stage definitions
Write one definition per stage (Lead → MQL → SQL → Opportunity → Closed → Live customer),
each with: entry criteria (observable), exit criteria, owner, and max dwell time. A record is
in **exactly one** stage. Publish the definitions; changes are versioned and 🟡 (standing rule).

### 2. Handoff SLAs (where leads die)
| Handoff | Default SLA | Leak metric |
|---|---|---|
| Inbound → qualified | per `lead-response` policy | untouched-lead count |
| MQL → SQL (sales accepts/rejects) | 1 business day | MQL age > SLA |
| SQL → first meeting | 5 business days | stalled-SQL count |
| Closed-won → CS onboarding | 2 business days | orphaned-customer count |
Every rejection needs a reason code — rejected-without-reason is the #1 hidden leak.

### 3. Routing rules
Keep them few, written, and versioned (with `lead-response`'s policy). Log rule changes via
`audit-log` (standing-rule change, 🟡).

### 4. Attribution (honest, multi-model)
Run **at least two models** (first-touch + last-touch minimum; add linear/U-shaped when data
allows) and report them side by side with each model's blind spot named. Never present a single
model as truth. Output: channel → pipeline → revenue table + confidence caveats + the
"unattributable" bucket sized explicitly (hiding it inflates every channel).

### 5. CRM hygiene audit
Score: % records with owner · % with stage · % with next step + date · % stale (no activity
30/60/90d) · duplicate rate. Output a fix-list ranked by revenue impact. Mass updates/merges
to a live CRM are 🟡 — propose the change-set, human approves.

## Hard rules
- Definitions and SLAs are **standing rules**: changing live ones needs human approval + an audit-log entry + notification to affected departments (CRO/CMO/CS).
- Never fabricate funnel numbers; missing data is reported as missing, with the query that would fill it.
- Attribution informs budget *recommendations* — spend changes are the human's call (CFO consulted).
- No PII in reports — aggregate or ID-referenced only.

## Red flags
- *"Last-touch says paid search wins, ship it."* → One model is an opinion; two models + blind spots is analysis.
- *"I'll clean the CRM while I'm in there."* → Mass mutations are gated; propose the change-set.
- *"Everyone knows what an MQL is."* → Ten people, eleven definitions. Write it down.

## Verification before claiming done
Stage definitions are mutually exclusive and observable; every handoff has SLA + leak metric +
current value (or a named data gap); attribution shows ≥2 models + unattributable bucket;
hygiene fix-list is ranked by revenue; all standing-rule changes logged and approved.
