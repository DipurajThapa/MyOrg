# Production Readiness Gap Closure — 2026-08-06

Decision: **RELEASE BLOCKED BY LIVE/HUMAN EVIDENCE, NOT BY AN UNIMPLEMENTED LOCAL CONTROL**  
Scope: three product capabilities only—governed intake, controlled work, and operational visibility.

## Audit method

Each stale `Partial` or `Missing` claim was traced from UI to signed gateway, service authorization,
organization-keyed transaction, audit evidence, failure mode, recovery path, and executable test.
“Implemented” below means code and local automated evidence exist. It never means deployed,
externally reviewed, or human-approved.

| Area | Root cause found | Correction and exact evidence | Verified result | Remaining release gate |
|---|---|---|---|---|
| operator UI/UX | state and intake lived only in React memory; browser had no trusted runtime bridge | durable view/filter/sort/scroll/project state in migration 002; signed worker proxy; intake create/update/reset UX; UI lint/build/render/proxy-signature tests | **Implemented locally**; restart, stale-write, user/tenant isolation, defaults and reset pass | configure runtime secrets/URL; human visual, keyboard, screen-reader, zoom and error UAT |
| identity, tenancy and role binding | the old runtime accepted logical role names; Site identity was not mapped to server membership | `identity_bindings`; HMAC body-bound gateway assertion with issuer/audience/timestamp/nonce; DB actor and roles reread on every request; replay table; disable/suspend controls | **Implemented locally**; signature tamper/replay, role refresh and tenant tests pass | production identity lifecycle, MFA/session, access review and break-glass evidence |
| persistence and recovery | UI and operating events were absent from the SQLite authority; no scheduled retention/backup unit | migrations 002/003; WAL/FULL transactions; optimistic revisions; two hash chains; verified online backup; exact-target restore; backup/maintenance systemd timers | **Implemented locally**; upgrade, restart, concurrency, tamper, corrupt-backup and pre-restore tests pass | provision durable volume/backup store; approve retention/legal hold; measure live RPO/RTO |
| ingress/egress gateway | connector policy existed, but live authorization, enable/disable and receipt reconciliation were not modeled | migration 003 authorization/reconciliation records; human-system-admin authorization, expiry, scope and secret-reference validation; enable gate; atomic revoke-and-disable; receipt reconciliation; existing allowlists, exact approval, idempotency and webhook replay defense | **Control plane implemented locally**; fixture effects and control-plane tests pass fail-closed | choose first provider; perform human OAuth; validate scopes, redirect/DNS behavior, rotation, provider receipt and reconciliation in non-production |
| security/privacy/accessibility | review documents existed but several executable controls and current evidence were absent | updated threat boundary; gateway/replay/tenant tests; CodeQL workflow; SBOM/checksum/credential scan; structured log minimization; semantic UI and security headers; one credential-shaped historical value redacted | **Automated/local review implemented**; current secret scan passes | external security/DAST/load review; legal privacy decisions; human WCAG 2.2 AA evaluation and owner sign-offs |
| observability and operations | health/log examples existed without protected metrics, alerts, retention executor or incident ownership workflow | `/metrics` bearer protection and low-cardinality counters; JSON logs with trace/internal actor only; `/readyz` DB + both audit-chain verification; Prometheus alerts; SLO/incident/recovery runbook; maintenance and backup timers | **Implemented locally**; metric auth/content and readiness tests pass | assign service/on-call owners; connect log/metric sinks; baseline and exercise alerts in the target environment |
| CI/CD, deployment, UAT and rollback | a prose plan existed with no enforced CI evidence bundle or machine-checkable release gate | least-privilege GitHub Actions acceptance + CodeQL; source manifest, SBOM, secret scan and checksums; fail-closed release record validator; hardened service/proxy/timers; UAT/deploy/rollback scripts and evidence schema | **Pipeline and controls implemented; not executed remotely** | GitHub run, target provisioning, owner-approved UAT, deployment smoke and timed rollback exercise |

## Project intake process and required documents

1. **Triage — `00-intake-brief.md`:** sponsor, decision owner, affected user, measurable outcome,
   authority and stop conditions. Incomplete context remains Draft.
2. **Clarify — `01-discovery-evidence.md`:** separate facts, assumptions, unknowns and evidence;
   cap the MVP at the three named capabilities.
3. **Map — `02-value-stream-and-journey.md`:** observe touch/wait/rework/handoffs for five real
   intakes; map customer and operator moments without inventing timings.
4. **Specify — `03-requirements-data-contract.md`:** trace outcome → requirement → interface →
   identity/tenant rule → test → release evidence; define every bidirectional receipt.
5. **Control — `04-risk-and-controls.md`:** name data class, retention, threat, human gate,
   rollback trigger, owner and expiry for every exception.
6. **Validate — `05-test-release-readiness.md`:** attach immutable CI, UAT, accessibility,
   recovery, connector, alert, deployment and rollback evidence, then complete
   `templates/release-evidence/gate-record.template.json`.

Ready is a controlled state: all five minimum fields and all six document controls must be true.
The runtime enforces this server-side and stores the operator, revision and tamper-evident event.

## Release decision

The local development gaps in the supplied table are closed and regression-tested. Release is
still **BLOCKED** because software cannot invent a provider choice, legal retention decision,
external review, human accessibility result, production credentials, deployment approval, or
rollback observation. `scripts/release_gate.py` rejects the provided template until all 12 live
checks, seven evidence references, six named sign-offs, and an immutable source revision agree.
