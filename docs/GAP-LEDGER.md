# Production-Readiness Gap Ledger

One row per gap from the 2026-07-14 production evaluation, each with its disposition and
evidence. Statuses: **BUILT** (capability exists, test-covered) · **DOCUMENTED** (governed by
written policy/playbook — no build needed or possible) · **BLOCKED-ON-HUMAN** (requires an
action only the human can take) · **DEFERRED** (deliberately not built — reason given; needs
its own approval). Maintained by the Chief of Staff; update when a disposition changes
(human-approved, like any shared-rule change).

## Tier 0 — critical

| # | Gap | Disposition | Where |
|---|---|---|---|
| 0.1 | Connectors unauthorized — everything advisory-only | **LOCAL GATEWAY BUILT; LIVE CONNECTOR BLOCKED-ON-HUMAN.** Fixture proves allowlists, exact approval, idempotency, receipts and signed webhook replay defense; no real HTTP/OAuth adapter is admitted | `runtime/connectors.py`, `docs/SECURITY-THREAT-MODEL.md` |
| 0.2 | No speed-to-lead (capture→qualify→route→respond under SLA) | **BUILT** — skill + policy + rubric + templates + worked run + computed-SLA test | `.claude/skills/lead-response/`, `examples/revenue-ops/`, `tests/module-lead-response.sh` |
| 0.3 | No billing→AR→dunning→collections | **BUILT** — aging, cause-first gated dunning ladder, failed-payment recovery, escalation | `.claude/skills/ar-collections/` (CFO) |
| 0.4 | No renewals / churn-prevention engine | **BUILT** — health score, T-90 renewal pipeline, save-plays, CSAT/NPS + a new department to own it | `.claude/skills/renewals-retention/`, `.claude/agents/customer-success.md` |
| 0.5 | No action/approval audit trail | **BUILT** — append-only JSONL store, schema, skill, §8 governance rule, COO oversight, injection-safe append | `logs/`, `.claude/skills/audit-log/`, `company/operating-principles.md` §8 |

## Tier 1 — high

| # | Gap | Disposition | Where |
|---|---|---|---|
| 1.1 | No RevOps (funnel definitions, handoff SLAs, attribution, CRM hygiene) | **BUILT** — agent + skill | `.claude/agents/revops.md`, `.claude/skills/funnel-attribution/` |
| 1.2 | No proactive Customer Success (vs. reactive support) | **BUILT** — agent + skill; boundary written into both customer agents | `.claude/agents/customer-success.md`, `.claude/agents/head-of-customer.md` |
| 1.3 | No reputation/review management or crisis comms | **BUILT** — triage table, severity-leveled crisis playbook, consent-gated testimonials | `.claude/skills/reputation-management/` (CMO) |
| 1.4 | No security/GRC program (enterprise-deal blocker) | **BUILT** — agent + skill: readiness, questionnaires (honest-answer rule), vendor security, access reviews (🔴 execution) | `.claude/agents/security-grc.md`, `.claude/skills/grc-readiness/` |
| 1.5 | No privacy program (DSR/GDPR/CCPA/breach) | **BUILT** — DSR statutory clocks, 72h breach runbook, consent map, retention schedule | `.claude/skills/privacy-program/` (CLO) |
| 1.6 | No deal desk / discount guardrails | **BUILT** — discount approval matrix, margin floors, term-screening lanes | `.claude/skills/deal-desk/` (CRO+CFO) |

## Tier 2 — growth ceiling / efficiency

| # | Gap | Disposition | Where |
|---|---|---|---|
| 2.1 | Demand-gen depth (paid ads, landing/CRO, nurture, referral) | **BUILT** — with kill rules, gated spend/sends | `.claude/skills/demand-gen/` (CMO) |
| 2.2 | No KPI tree / LTV/CAC / leak detection / experiments | **BUILT** — north-star tree, unit-economics discipline, quarterly leak sweep, decision-rule experiments | `.claude/skills/kpi-tree/` (Data+RevOps+CFO) |
| 2.3 | 24/7 coverage | **DOCUMENTED** — after-hours play: clock-pause rules, optional scheduled pre-drafting (§5 cadences), never unattended sends. Full autonomy needs the fast-lane (see OS-2) | `company/playbooks.md`, `lead-response` policy |
| 2.4 | Contract lifecycle (auto-renew traps, obligations) | **BUILT** — obligations register, T-120 calendar, notice-window alerts | `.claude/skills/contract-lifecycle/` (CLO+COO) |
| 2.5 | Quota/comp/commission mechanics | **DOCUMENTED** — owned by `revops` charter (with CHRO `comp-analysis` + CFO); dedicated skill deferred until a real comp plan exists to encode (capability gate) | `.claude/agents/revops.md` |

## OS / structural

| # | Gap | Disposition | Where |
|---|---|---|---|
| OS-1 | No outcome instrumentation (activity ≠ progress) | **BUILT** — the KPI tree is the outcome yardstick; every department KPI must trace to a branch; leak sweep quantifies misses | `.claude/skills/kpi-tree/` §1 |
| OS-2 | Approval-latency bottleneck (no fast lane) | **DEFERRED — needs its own approval.** Design direction documented: pre-approved, template-bound, logged auto-acknowledgments for a narrow set of time-critical moments. Not built: it weakens the 🟡 gate and the human must opt in explicitly | this ledger (design note) |
| OS-3 | No secrets-management guidance | **DOCUMENTED** | `company/connectors.md` §Secrets |
| OS-4 | No degraded-mode/fallback rules | **DOCUMENTED** — fall back to files, label staleness, never fabricate, pause SLA clocks | `company/connectors.md` §Degraded mode |
| OS-5 | No data classification / PII retention for memory | **DOCUMENTED** — 4-class table + per-store rules | `company/memory-and-learning.md` §6 |
| OS-6 | No executable orchestration, replay, or bounded retry | **BUILT — FIRST INCREMENT**: deterministic DAG, append-only events, immutable input revision, evidence checks, idempotency, retry/cycle stops | `runtime/`, `tests/module-controlled-runtime.sh` |
| OS-7 | Approval and red-action gates were prompt-enforced | **BUILT — IDENTITY-BOUND LOCAL SERVICE**: signed short-lived actor tokens, DB-bound roles, human-only distinct checker, exact action hash/expiry/single-use consumption; managed production IdP still blocked | `runtime/auth.py`, `runtime/service.py`, `tests/test_production_foundation.py` |
| OS-8 | No provider/tool execution gateway | **BUILT FOR FIXTURE / LIVE PROVIDER BLOCKED**: fail-closed connector registry, host/action/secret controls, atomic receipt, webhook verification; real connector/OAuth needs separate human authorization and provider tests | `runtime/connectors.py`, `runtime/connector-manifests/fixture.json` |
| OS-9 | No multi-directional internal information exchange | **BUILT — SECOND INCREMENT**: typed path+hash envelopes, authorized participants, reverse-direction replies, and adjacent-DAG handoffs; raw/restricted payloads excluded | `runtime/company_runtime.py`, `docs/EXCHANGE-MAKER-CHECKER-AUDIT.md` |
| OS-10 | No maker-checker quality gate | **BUILT — AUTHENTICATED LOCAL CONTROL**: immutable submissions plus DB-bound maker/human decision owner, exact action hash, expiry, single-use execution and atomic receipt; real-IdP UAT pending | `runtime/workflows/maker-checker-gold-run.json`, `runtime/service.py`, `tests/test_production_foundation.py` |
| OS-11 | No canonical project intake, value stream, or customer journey | **BUILT — THIRD INCREMENT**: six-stage intake pack, SIPOC/current/future map, data contract, risk, traceability, test and release gates | `docs/PROJECT-INTAKE-AND-PRODUCTION-LOOP.md`, `templates/project-intake/`, `tests/module-project-intake.sh` |
| OS-12 | No usable operator surface | **PARTIAL — SIGNED-IN READ-ONLY RELEASE CANDIDATE**: governed intake/work/approval/flow views, fail-closed authentication, security headers and accessibility structure pass local checks; API write integration and human accessibility UAT remain blocked | MyOrg Control Center Site, `docs/SECURITY-PRIVACY-ACCESSIBILITY-REVIEW-2026-08-06.md` |
| OS-13 | No production application foundation | **LOCAL CONTROL FOUNDATION COMPLETE / RELEASE BLOCKED**: signed UI identity binding, org/user durable intake and preferences, three SQLite migrations, two audit chains, recovery/timers, connector authorization/kill-switch/reconciliation, protected metrics/alerts, CI/CodeQL/SBOM/scan and a fail-closed release record are test-covered. Production identity lifecycle, provider OAuth/adapter, external/human review, live monitoring, UAT, deploy and rollback evidence remain unrun | `runtime/`, `.github/workflows/ci.yml`, `docs/PRODUCTION-READINESS-GAP-CLOSURE-2026-08-06.md` |

## Minimal-agents check (sprawl control)

3 agents added (customer-success, revops, security-grc) — exactly the minimal set from the
evaluation; every other gap became a *skill inside an existing department* or a *policy
section*, not headcount. 17 agents total; org integrity enforced by `tests/core.sh` C3.

## Standing honesty notes

- **BUILT means the stated local behavior is executable and tested.** It does
  not mean battle-tested against live data; that starts when connectors (0.1) are authorized.
- **PARTIAL or MISSING blocks production-ready wording.** A polished prototype or passing local
  suite is not evidence of identity, recovery, deployment, UAT, or live-system safety.
- All sends, spend, publishing, filings, deletions, and access changes remain human-gated
  regardless of any skill in this ledger — no capability here overrides §3 of `CLAUDE.md`.
