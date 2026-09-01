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

## Standing a new company up (ARCH-06)

One command creates the store, the organization, the first operator and a sign-in token.
It is safe to run twice.

```bash
export MYORG_AUTH_SECRET="$(python -c 'import secrets;print(secrets.token_hex(32))')"
python -m runtime.admin bootstrap   --org default --name "MyOrg"   --operator dipuraj --operator-name "Dipuraj Thapa"
```

Keep `MYORG_AUTH_SECRET` — every token is signed with it, and without it nobody can sign
in. The printed token is a bearer credential with a short life: keep it out of shell
history, logs and tickets, and issue more with `admin issue-token`.

The first operator gets `decision-owner` and `maker`: enough to answer gates and prepare
work. Anything wider is a deliberate second step:

```bash
python -m runtime.admin actor --org default --id auditor-one   --type human --name "Auditor" --role auditor
```

Then run the parts you need:

```bash
export MYORG_DB=$PWD/runtime/data/myorg.db
python -m runtime.api                 # the governed API the web app talks to
python -m runtime.projection          # mirror runs into the operator read model
python -m runtime.scheduler --once    # drive whatever can move
python -m runtime.admin verify        # integrity, migrations, event counts
```

`runtime/projection` is one-way by design: the run log is the system of record for
execution, the database is identity plus the read model. If the two ever disagree, the log
is right and the projection needs re-running.


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

## Approvals waiting

`MyOrgApprovalUnanswered` means a run has stopped and is waiting on a person — for over four
hours. This is not an error; it is the governance model working, and the alert exists because
"stopped, waiting for you" and "nothing to do" look identical from outside.

See the queue with `GET /v1/decisions` or the Control Center, and answer it there. If the
queue is long because nobody is on duty, that is a staffing decision, not a runtime one:
pause the schedules feeding it (`PUT /v1/schedules/{id}/status`, `{"enabled": false}`) rather
than letting work pile up against an absent approver. Never widen the green band to clear a
backlog — the gate classification is in `runtime/policy.json` and changing it changes what
the company may do unattended, forever.

## Stalled runs

`MyOrgRunsStalled` is the dangerous quiet: a run that can no longer move and is *not* waiting
on anybody. Nobody has been asked for anything, so nobody will notice.

`python -m runtime.health` names them and says why. The usual causes are a step held by a
worker that died (reclaim with `expire-claim`), a dependency that never completed, or an
exhausted cycle budget (`blocked_cycle_limit` — terminal today, REC-11). Check the scheduler
is alive first: `systemctl status myorg-scheduler`, or `Get-ScheduledTask MyOrgScheduler`.

## Trigger queue backlog

`MyOrgTriggerQueueBacklog` means work is arriving faster than it is being planned. The queue
refuses new work at 50, so this fires with room to spare.

Almost always the scheduler has stopped — check it is running before anything else. If it is
running and still behind, a provider is retrying in a loop: find the source with
`GET /v1/schedules` and the `trigger_intake.source_ref` column, and pause that trigger.
Planning costs money, so a backlog left alone is a bill, not just a delay.

## A run is spending too much

`MyOrgRunSpendHigh` fires at $3; the ceiling parks the run at $5 (`MYORG_RUN_CEILING_USD`).
The gap is deliberate — it is time to look, not yet time to stop.

Almost always a retry loop: a step failing its acceptance criteria over and over, each
attempt costing a full dispatch **plus** a grading pass. Grading is about 40% of the bill,
so three retries is roughly six paid calls. `python -m runtime.health` names the run;
`spend_usd` on the run and on each step says where it went.

When the ceiling does park a step, that step sits at `awaiting_approval` with the figures in
its reason. Approving it buys the next step — it does not lift the ceiling, so a genuinely
expensive run asks again. If the work is worth it, raise `MYORG_RUN_CEILING_USD` and restart
the scheduler rather than approving repeatedly.

**A run out of *cycles* is a different thing** and is not about money:
`python -m runtime.company_runtime extend-budget <run> --cycles 10 --approver <you>
--request-id <id>`. Completed steps are kept; nothing is re-run.

## Autonomy metrics blind

`MyOrgAutonomyMetricsBlind` (`myorg_runtime_snapshot_ok 0`) means the runtime cannot read its
own state. **Treat every other autonomy alert as unreliable until this clears** — they would
all go quiet for the same reason a healthy company does. `myorg_runtime_snapshot_errors_total`
counts the failures; the API log names the source that raised.

## Rotating the signing key

`MYORG_AUTH_SECRET` accepts two comma-separated keys. Tokens are always signed with the
first; either is accepted on the way in. That overlap is the whole procedure:

1. `MYORG_AUTH_SECRET="<new>,<old>"` — restart. Existing tokens keep working; new ones use
   the new key.
2. Wait **15 minutes** (the maximum token lifetime), so nothing signed with the old key is
   still valid.
3. `MYORG_AUTH_SECRET="<new>"` — restart. The old key is now dead.

Both keys must be at least 32 bytes. Three keys are refused: an overlap is two, and a third
means an earlier rotation was never finished.

**After a suspected leak, skip step 1.** Set the new key alone and accept that everyone is
logged out — that is the correct trade when the old key may be in someone else's hands. The
overlap exists so that *planned* rotation is not an outage, which is what makes it something
people will actually do on a schedule.

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
