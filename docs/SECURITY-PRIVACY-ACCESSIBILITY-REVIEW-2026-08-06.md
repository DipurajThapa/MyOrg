# Security, Privacy, and Accessibility Review — 2026-08-06

Decision: **CONDITIONAL / PRODUCTION RELEASE BLOCKED**

## Executed evidence

| Review | Result | Evidence |
|---|---|---|
| authentication/authorization adversarial tests | Passed locally | signed token tamper/expiry/revocation, DB-bound role change, unauthorized role, human-only decision, self-approval denial |
| tenant isolation | Passed locally | cross-organization run lookup returns not found; principal organization is never supplied by request body |
| approval and effect integrity | Passed locally | exact hash mismatch denied; single-use approval; receipt and consumption atomic; duplicate/different requests denied |
| connector security | Passed locally | non-HTTPS, localhost/private target, embedded secret value, unknown field, action mismatch, disabled connector denied |
| webhook security | Passed locally | signature tamper, stale timestamp, and nonce replay denied |
| database/recovery | Passed locally | SQLite integrity and event hash chain; backup checksum; restore; corrupt-backup refusal; pre-restore backup |
| API boundary | Passed locally | auth required, role denial, tenant isolation, body/content checks, deny-by-default CORS, browser-security headers |
| production dependency scan | Passed | `npm audit --omit=dev --audit-level=moderate`: 0 known vulnerabilities after Next 16.3.0 upgrade |
| static secret-pattern inspection | Passed after remediation | one credential-shaped value in a historical research artifact was redacted without disclosure; `scripts/release_evidence.py` now passes |
| UI structure | Passed automated checks | signed-in render, skip link, main landmark, status text, security headers, lint/build/artifact test |

## Privacy review

Data minimization is enforced at the service boundary: run metadata accepts only `public` or
`internal`; confidential/restricted raw content must remain in separately governed artifacts.
Events and approvals carry bounded references and SHA-256 digests. Connector inventory omits
the secret-reference name, and secret values are prohibited in source manifests.

UI preferences and project intakes are organization/user scoped. Structured operational logs
contain only trace IDs, internal actor slugs, categories, resource IDs and bounded metadata; they
exclude authenticated email subjects, headers, tokens, bodies, project titles and outcomes.

Production remains blocked until the owner documents data controller/processor roles, legal
basis, residency, retention/deletion periods for runs/events/backups/nonces, data-subject and
incident processes, and the first connector's data-processing terms. A privacy owner must sign
the completed intake control document; code cannot decide these legal/business facts.

## Accessibility review

Implemented: semantic navigation/main landmarks, visible focus, skip link, keyboard-operable
native controls, focus movement after view changes, required fields, non-color status text,
responsive layout, reduced-motion handling, and live status notification.

Automated HTML checks do not establish WCAG conformance. A human must complete keyboard-only,
zoom/reflow, contrast, error-identification, screen-reader, and cognitive/plain-language UAT
against WCAG 2.2 AA. The cloud visual inspection was infrastructure-blocked; production build
and rendered-HTML checks passed, but that does not replace the human review.

## Security limitations requiring human/external evidence

- No external penetration test, threat-led red team, DAST, or load test has run.
- Local HMAC tokens are a deployment-neutral proof; production should validate a managed IdP,
  asymmetric key rotation, provisioning/deprovisioning, MFA/session policy, and break-glass use.
- No real provider connector exists. OAuth scopes, redirects/DNS behavior, provider receipts,
  reconciliation, and secret rotation remain untested.
- Metrics, alerts, JSON logs, timers and an SLO/incident runbook are implemented locally; no
  production sink, baseline, owner assignment or alert exercise evidence exists.
- UI CSP retains `unsafe-inline` for the current framework bootstrap; this is recorded residual
  risk and must be retested if nonce support is introduced.
- A least-privilege CodeQL workflow and standard-library runtime SBOM generator are present; the
  workflow has not yet executed on GitHub and independent review remains a release gate.
- The full UI toolchain scan was reduced from 45 advisories to four transitive `undici` advisories
  under Cloudflare Miniflare/Wrangler. They are development-only, have no upstream fix in the
  current compatible toolchain, and are excluded from production dependencies. Do not expose the
  local development server to untrusted networks; rescan on every dependency update.

Security owner, privacy owner, accessibility tester, and product decision owner must each sign
the UAT/release record. Any accepted exception needs an owner, expiry date, compensating control,
and explicit risk-acceptance decision.
