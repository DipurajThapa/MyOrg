---
name: kpi-tree
description: >
  Build the company's measurement backbone: a north-star KPI tree, unit economics (MRR/ARR,
  LTV, CAC, payback, NRR, cohorts), a revenue-leak detection sweep, and experiment design
  with success criteria. Owned by head-of-data with revops/cfo-finance. Use to define what
  the business measures, find where revenue leaks, or design a test before spending on a
  change. This is also how the OS measures outcomes instead of activity.
---

# KPI Tree & Unit Economics — measure outcomes, find the leaks

## When to use
Defining/refreshing company metrics, board-metric prep, "where are we losing money?",
LTV/CAC questions, or before any growth experiment.

## 1. The north-star tree
Pick **one** north-star metric (the value customers get, in volume). Decompose it into 3–5
driver branches (e.g. acquisition × activation × retention × expansion), each with: owner
department, current value, data source, and refresh cadence. Every department KPI must trace
to a branch — a metric with no path to the north star is activity, not progress. **This is the
OS's outcome instrumentation:** agent work is judged against tree movement, not files produced.

## 2. Unit economics (compute, show work, state caveats)
| Metric | Definition discipline |
|---|---|
| MRR/ARR | report the **level**, plus the movement bridge (net-new = new + expansion − contraction − churn) — never the delta alone as if it were MRR |
| NRR | revenue from last-year's cohort ÷ their year-ago revenue |
| LTV | margin-based, not revenue-based; state the churn assumption |
| CAC | fully-loaded (spend + people), split organic vs. paid |
| Payback | months to recover CAC from margin; the honest cash metric |
| Cohorts | retention curves by signup month — the truth about product value |
Never fabricate inputs; missing data is named with the query/export that would fill it.

## 3. Revenue-leak sweep (run quarterly)
Walk each stage and quantify the leak: untouched leads (`funnel-attribution`) · SLA-breached
responses (`lead-response` log) · stalled SQLs · discount creep (`deal-desk` trend) · failed
payments + aged AR (`ar-collections`) · involuntary churn · renewals lapsed without a play
(`renewals-retention`) · orphaned closed-won accounts. Output: leak table (stage → $/period →
owner → fix), biggest first.

## 4. Experiment design (before spend, not after)
Every experiment states: hypothesis → metric (a tree branch) → minimum detectable effect →
sample/duration → decision rule ("if X by date, we do Y"). No experiment without a decision
rule — that's how "tests" become permanent unmeasured spend. Launching real-audience
experiments is 🟡 — log via the `audit-log` skill (`experiment.launch`, `approval: pending`
until the human's yes).

## Hard rules
- Definitions live in one place (this tree); other docs link, never redefine.
- Every number in a readout carries: source, as-of date, and caveat. Correlation ≠ causation is stated where relevant.
- The tree itself is a standing artifact — changing the north star or branch structure is a human decision (🟡), logged via the `audit-log` skill (`kpi-tree.change`).

## Red flags
- *"Revenue is the north star."* → Revenue is the result; the north star is the customer-value driver you can act on weekly.
- *"LTV/CAC is 5, we're great."* → With which churn assumption and whose CAC loading? Show the work.
- *"We'll define the success metric after the test."* → That's a story, not an experiment.

## Verification before claiming done
The tree has one north star, ≤5 branches, every branch owned + sourced; unit economics show
their bridges/assumptions; the leak table sums and ranks; every experiment has a decision rule.
