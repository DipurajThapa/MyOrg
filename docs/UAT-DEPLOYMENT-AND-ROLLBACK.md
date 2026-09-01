# UAT, Deployment, and Rollback Control

Status: executable plan prepared; human and production evidence not run  
Release decision owner: human system owner

## 1. Project intake gate

Before UAT, create/copy the six documents under `templates/project-intake/` and resolve this
minimum pack:

| Stage | Required document | Blocking decision |
|---|---|---|
| Triage | `00-intake-brief.md` | sponsor, affected user, outcome and decision owner known |
| Clarify | `01-discovery-evidence.md` | facts/assumptions and three-capability scope approved |
| Map | `02-value-stream-and-journey.md` | current-state users validate observations; timings not invented |
| Specify | `03-requirements-data-contract.md` | identities, data rights, retention, deployment target, first connector defined |
| Control | `04-risk-and-controls.md` | security/privacy/accessibility risks owned; exceptions explicitly accepted |
| Validate | `05-test-release-readiness.md` | tests/UAT/recovery/deployment/rollback evidence passed and signed |

## 2. UAT script

Use production-like accounts in a non-production environment. Record timestamp, environment,
build/commit, tester, role, result, evidence reference, and defect ID for every scenario.

| ID | Actor | Scenario | Expected result |
|---|---|---|---|
| UAT-01 | sponsor/operator | incomplete intake, persist draft, restart, then complete all documents | missing fields block Ready; draft survives restart; complete record reaches Ready with revision/audit evidence |
| UAT-02 | unauthorized viewer | create run / list admin connectors | 403; security event observable without token/body disclosure |
| UAT-03 | second organization | guess run/approval IDs | no existence disclosure or cross-tenant data |
| UAT-04 | maker agent | propose a yellow exact effect | pending approval includes target/payload reference, digest and expiry |
| UAT-05 | same requester / agent checker | approve own action | denied |
| UAT-06 | human decision owner | approve changed digest, then exact digest | changed denied; exact accepted once |
| UAT-07 | gateway service | retry same effect, then mutate same key | same receipt returned; changed request rejected; no duplicate effect |
| UAT-08 | security tester | forge/stale/replay webhook | all denied and observable |
| UAT-09 | operator | corrupt/tamper event or backup copy | readiness/restore fails closed |
| UAT-10 | accessibility testers | keyboard, screen reader, 200/400% zoom, contrast/errors | WCAG 2.2 AA findings closed or explicitly blocked |
| UAT-11 | operations | stop/restart service and exercise backup/restore | state retained; approved RPO/RTO met; reconciliation complete |
| UAT-12 | release owner | execute deploy smoke then rollback | both versions verified; rollback time and evidence captured |
| UAT-13 | two sessions/same user | change the same view/project revision concurrently | exactly one write succeeds; stale writer receives 409 and cannot overwrite silently |
| UAT-14 | connector owner | authorize least scopes, enable, revoke, reconcile receipt | enable denied before authorization; revoke disables atomically; no effect is complete without reconciliation evidence |

Exit requires all severity-1/2 defects closed, no unresolved auth/tenant/effect-integrity issue,
and signatures from product, security, privacy, accessibility, and operations owners.

## 3. Deployment sequence

1. Freeze exact source revision and attach full test, dependency, secret-scan, migration and
   artifact checksums.
2. Provision managed identity, TLS/reverse proxy, application user, database/backup paths,
   monitoring and secret injection. Never place `MYORG_AUTH_SECRET`, `MYORG_GATEWAY_SECRET`, or
   `MYORG_METRICS_TOKEN` in source, browser output, logs, or command history.
3. Restore a recent scrubbed backup in non-production and run `runtime.admin verify`.
4. Apply migrations, bootstrap roles through the approved identity-provisioning process, and
   keep all real connectors disabled.
5. Start the API on loopback behind TLS; verify `/healthz`, authenticated `/v1/me`, tenant and
   negative authorization tests, security headers, logs, alerts and backup job.
6. Deploy the signed-in UI owner-only; bind each platform identity to one runtime actor; configure
   `MYORG_API_URL` and `MYORG_GATEWAY_SECRET`; verify sign-in/sign-out, signed body/nonce rejection,
   keyboard path, durable state, no secrets/tokens in browser output, and deny-by-default CORS.
7. Run UAT. A human approves go-live only after seeing the evidence and residual-risk ledger.
8. Record one least-privileged human OAuth authorization while the connector is disabled; enable
   it through the human system-admin endpoint, exercise one proposed write, reconcile its provider
   receipt manually, then prove revocation disables the connector atomically.

No deployment has been executed by this work. Publishing the Site, provisioning an IdP,
authorizing OAuth, or changing production state requires an explicit human decision.

## 4. Rollback procedure

Triggers: critical auth/tenant breach, unauthorized or duplicate effect, integrity failure,
unreconciled provider response, severe accessibility blocker, SLO breach, failed migration, or
decision-owner instruction.

1. Disable connector registrations and stop new writes; preserve logs/evidence.
2. Route UI/API traffic to the last approved application version. Do not roll the database back
   merely because code rolled back.
3. If data rollback is approved, capture a pre-restore backup, verify source checksum/integrity,
   state the exact target, restore, verify event chain, and reconcile every provider effect after
   the backup point.
4. Re-run health, auth, tenant, idempotency, approval, connector and journey smoke tests.
5. Record start/end time, approver, versions, backup hashes, affected effects, reconciliation and
   incident/lesson decision.

The local automated suite proves checksum refusal and a successful restore with a pre-restore
copy. It does not prove production RPO/RTO, traffic reversal, alerting, or provider reconciliation.
