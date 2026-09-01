---
name: demand-gen
description: >
  Fill the top of the funnel deliberately: paid-ads operations (structure, budgets, creative
  rotation — all spend human-approved), landing-page/conversion-rate audits, lifecycle nurture
  sequences, and referral/partner program design. Owned by cmo-marketing. Use when organic
  isn't enough, landing pages underconvert, leads go cold between touches, or you want
  customers recruiting customers. Complements marketing:campaign-plan / seo-audit /
  email-sequence — this is the always-on demand engine, not a one-off campaign.
---

# Demand Gen — the always-on engine behind the campaigns

## When to use
Standing paid-ads program, landing-page conversion problems, lead-nurture gaps between
first touch and sales-ready, or referral/partner program design.

## 1. Paid-ads operations (spend is always the human's)
- **Structure:** one campaign per objective, ad groups by intent theme, ≥2 creatives rotating
  per group; UTM discipline so `funnel-attribution` can see everything.
- **Budget guardrails:** proposed budgets come with expected CAC vs. the `kpi-tree` payback
  ceiling; a kill rule per campaign ("pause if CAC > $X after $Y spend") is set **before** launch.
- **Cadence:** weekly readout — spend, CPL, CAC by channel, against the kill rules.
- **Gate:** committing/changing spend, launching/pausing campaigns = 🟡, logged via `audit-log`.

## 2. Landing-page / conversion audit
Score each page: message-match to the ad/source · one clear CTA · form friction (fields vs.
what `lead-response` scoring actually needs) · load/mobile sanity · proof elements near the CTA.
Output ranked fixes with expected impact; changes to live pages are 🟡 (publishing).

## 3. Lifecycle nurture
Map the gap between "not sales-ready" and "sales-ready": a 4–6 touch sequence per segment,
each touch = one useful thing (not "just checking in"), with an exit trigger to `lead-response`
scoring when intent signals appear. Sequences are standing automations → creating/altering
them is 🟡 — **and every send stays 🟡 per-send regardless**: scheduled runs may pre-draft the
next touch, never send it. (An auto-send fast-lane is deliberately **not** part of this skill —
see `docs/GAP-LEDGER.md` OS-2; it would need its own explicit human approval as a separate change.)

## 4. Referral & partner programs
Design: who refers (happy customers via `customer-success` health scores ≥8) · the ask moment
(post-value milestone, post-high-NPS) · the incentive (priced by CFO) · tracking (codes/links
via `funnel-attribution`). Program launch + incentive cost = human approval.

## Hard rules
- **No spend, no launch, no live-page change, no standing sequence without explicit human approval.** Prepare → show cost/copy/audience → wait for yes → log.
- Claims in ads/pages follow brand + truth rules (`cmo-marketing` governance): no invented numbers or testimonials; regulated claims → CLO.
- Every program names its `kpi-tree` branch and its kill/success rule up front.
- Audience data: no PII in URLs/UTMs; suppression lists respected.

## Red flags
- *"Broad targeting first, refine later."* → Later never comes; intent-first structure from day one.
- *"The sequence can send automatically, it's just nurture."* → Standing automations are 🟡 by constitution.
- *"More form fields = better leads."* → Only ask what scoring uses; friction is a silent leak.

## Verification before claiming done
Ads: structure + kill rules + UTM scheme documented, spend approved before anything runs.
Pages: scored audit with ranked fixes. Nurture: mapped sequence with exit triggers, approval
state explicit. Referral: economics priced, tracking defined. All gates logged.
