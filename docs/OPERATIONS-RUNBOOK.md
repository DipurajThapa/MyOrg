# Operations Runbook

Status: implementation complete; production ownership and live evidence pending  
Service owner: must be assigned before deployment  
Incident commander: on-call human operator

## Service levels and telemetry

Initial targets require owner approval after a non-production baseline: monthly API availability
99.9%, successful governed mutations 99.5%, and p95 API latency under 750 ms. `/healthz`
proves the process is responding; `/readyz` additionally verifies SQLite integrity, migrations,
and both event hash chains. `/metrics` is disabled unless `MYORG_METRICS_TOKEN` is injected and
returns only low-cardinality method/status/duration counters. Logs are one JSON object per line
with trace ID and internal actor slug; no headers, tokens, request bodies, email subjects, or
project content are logged.

Retention defaults: gateway/webhook/revocation records expire at their security window;
idempotency records retain 30 days through the hourly maintenance timer. Runs, approvals,
receipts, project intakes, audit events, and backups must not be automatically deleted until the
privacy owner approves the jurisdiction-specific retention and legal-hold policy. Backup lifecycle
must be configured at the storage platform after that decision.

## Runtime unavailable

1. Acknowledge the alert and freeze connector effects. Keep the UI fail-closed.
2. Check process state and the latest JSON logs by trace ID; never paste tokens or bodies.
3. Run `python -m runtime.admin --db "$MYORG_DB" verify` as the service account.
4. If process-only, restart once and verify health/readiness. If integrity fails, stop writes and
   invoke the approved recovery procedure—do not restore without the data-owner gate.
5. Record impact, start/end, affected organizations, evidence, and follow-up owner.

## Elevated errors

Correlate by status and trace ID, test a viewer denial and a maker-authorized request, verify disk
space and readiness, and roll application traffic back if errors began with a release. Do not roll
the database back as an application rollback shortcut.

## Authorization denials

Confirm whether failures align to an access change. Disable a suspected actor or suspend the
organization through offline administration, preserve logs, rotate gateway/issuer secrets through
the approved manager if compromise is plausible, and have Security review identity bindings and
role grants. Never reveal whether an ID exists across tenants.

## Backup, restore, and reconciliation

The six-hour timer creates a SQLite online backup, checksum manifest, and integrity/hash-chain
verification. Alert on timer failure and storage capacity. A restore requires the exact target,
approved backup, data-owner approval, a pre-restore copy, post-restore verification, and
reconciliation of every connector receipt after the backup point. Record measured RPO/RTO; the
local automated exercise is not production evidence.

## Support and incident record

Severity 1: tenant breach, unauthorized/duplicate effect, integrity failure, or widespread outage;
page Security, Operations, and the product decision owner immediately. Severity 2: sustained SLO
failure or one-organization outage; acknowledge within 15 minutes. All incidents need a timeline,
root cause, containment, recovery evidence, customer/privacy assessment, corrective owner, due date,
and a human-approved lesson before closure.
