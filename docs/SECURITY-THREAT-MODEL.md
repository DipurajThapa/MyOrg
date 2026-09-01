# Security Threat Model

Status: release-candidate baseline; production review pending  
Owner: Security & GRC  
Decision owner: human system owner  
Reviewed: 2026-08-06

## Scope and trust boundaries

The protected assets are organization data, actor/role bindings, action approvals, connector
authority, evidence hashes, audit events, database backups, and deployment credentials. The
three bounded capabilities are: (1) identity-bound API and persistence, (2) connector security
gateway, and (3) release evidence. Model/provider dispatch and real external connectors remain
outside this release candidate.

| Boundary | Untrusted side | Trusted side | Required control |
|---|---|---|---|
| browser → UI worker | public network/user input | authenticated Site | Sign in with ChatGPT, security headers, no runtime secrets, fail-closed proxy |
| UI worker → API | authenticated email and exact body | organization-scoped service | HMAC issuer/subject/audience/body signature, timestamp/nonce replay defense, DB identity/role lookup |
| service client → API | bearer token and JSON | organization-scoped service | short-lived signed actor, DB-bound roles, body/rate limits, deny-by-default CORS |
| agent → approval | proposed effect | human decision authority | exact action hash, expiry, maker/checker separation, single-use consumption |
| gateway → connector | target, payload reference, webhook | admitted connector | HTTPS/host/action allowlists, secret references, idempotency, signed replay-safe webhook |
| service → database | application mutations | SQLite state/audit chain | transactions, foreign keys, WAL/FULL sync, permissions, hash-chain verification |
| operator → recovery | backup/restore request | production state | checksum manifest, integrity check, exact target confirmation, pre-restore backup |

## Internal threats

| Threat / root cause | Impact | Implemented control | Residual / owner |
|---|---|---|---|
| agent or UI claims a privileged role | unapproved organization action | UI can assert only platform subject; organization/actor/roles are bound and reread from DB; tokens identify actors, not roles | production joiner/mover/leaver and access review — Security |
| maker approves own work | control bypass | only registered human `decision-owner`; requester and approver must differ | UAT with real identities — Product owner |
| admin misconfiguration or excessive role | cross-duty privilege | explicit role bindings, short token TTL, connector inventory redacts secret reference | joiner/mover/leaver process and quarterly review — Security |
| stale approval applied to changed content | wrong external effect | target/payload reference and SHA-256 form exact action hash; mismatch fails closed | provider-specific reconciliation — Connector owner |
| duplicate execution after retry | duplicate send/publish | request idempotency plus unique connector receipt; approval and receipt commit atomically | provider idempotency semantics — Connector owner |
| operator restores wrong/corrupt backup | data loss | manifest checksum, DB/hash verification, exact target confirmation, pre-restore copy | monitored production rehearsal and RPO/RTO approval — Operations |
| sensitive payload placed in logs | privacy breach | API only accepts public/internal run metadata; connector flow uses bounded references/hashes | DLP/log review and retention decision — Privacy owner |
| **dispatched step inherits the operator's own connectors** | a department reads the operator's mail, calendar or drive — data the company never granted it, through tools `tools.json` does not govern | `--strict-mcp-config` on every dispatch (`backends.py: DISPATCH_PROFILE`); the repository ships no `.mcp.json`, so a dispatch now loads none. Pinned by three tests, including for ungranted calls | found 2026-09-02 while measuring cost, not while reviewing security — the same check should be repeated for any future flag that widens a dispatch's environment — Security |

## External threats

| Threat | Impact | Implemented control | Residual / owner |
|---|---|---|---|
| token or gateway assertion theft/replay | account takeover | bearer TTL/revocation plus gateway exact-body signature, issuer/audience, 60-second nonce window, DB replay table and HTTPS-only deployment rule | rotate keys through approved manager and validate production platform identity lifecycle — Security |
| tenant-ID manipulation / IDOR | cross-organization disclosure | organization comes from verified principal; every data query includes organization key | independent penetration test — Security |
| SSRF / credential exfiltration via connector | internal network or secret compromise | exact credential-free HTTPS origin, port 443, host/action allowlists, private/reserved IP denial, secret names not values; live connectors require current human authorization and remain disabled on revocation | HTTP adapter, DNS rebinding and redirect controls must be proven for the selected provider — Connector owner |
| webhook forgery/replay | false inbound events | HMAC body signature, timestamp skew, nonce uniqueness, size bound | provider secret rotation and delivery reconciliation — Connector owner |
| injection / malformed input | code/data compromise | exact JSON fields, bounded slugs/references/hashes, prepared SQL, raw content excluded | fuzzing and external application test — Security |
| denial of service | outage | 256 KiB API body limit, connector response/time bounds, per-actor rate limit, bounded retries | edge/WAF rate limits, load test, alerting/SLO — Operations |
| UI clickjacking/content injection | unauthorized interaction | owner access, frame denial, CSP, no-store, referrer/permission policy | CSP currently needs `unsafe-inline` for framework bootstrap; replace with nonces when supported — Frontend owner |
| vulnerable dependency / supply chain | arbitrary compromise | exact lockfile, production dependency audit, upgrade to patched Next 16.3.0 | CI SBOM/signing and continuous scanning — Engineering |
| prompt/tool injection | agent exfiltration or unsafe action | external content is data; connector authority is outside model output; red actions unavailable | adversarial agent evaluation and provider sandbox — AI owner |

## Security invariants

1. Zero effects without an admitted connector and authorized service identity.
2. Zero yellow effects without a distinct human approval matching the exact action hash.
3. Zero cross-organization reads or writes, including through guessed identifiers.
4. Zero duplicate effects for one idempotency key.
5. Zero stored credentials in manifests, source, logs, or API responses.
6. A failed integrity, authorization, replay, dependency, recovery, or rollback check blocks release.

## Verification basis

The control selection is mapped to OWASP ASVS 5, the OWASP AI Agent Security Cheat Sheet,
NIST SP 800-218 SSDF, and WCAG 2.2. This is a targeted control review, not a claim of full ASVS,
SSDF, penetration-test, or WCAG conformance.

- https://owasp.org/www-project-application-security-verification-standard/
- https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html
- https://csrc.nist.gov/pubs/sp/800/218/final
- https://www.w3.org/TR/WCAG22/
