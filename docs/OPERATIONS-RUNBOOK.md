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

## Being told

Nothing below matters if nobody hears it. Every "somebody is needed" the runtime detects — a
decision waiting, a run that stopped, a run gone quiet, a lesson proposed — becomes a
**notice** in the outbox (`runtime/runs/_outbox.jsonl`, or `MYORG_OUTBOX`). The outbox does
not send anything by itself: sending is an outward action, so delivery is a command *you*
wire up once.

**The contract.** `MYORG_NOTIFY_COMMAND` names a command. The scheduler runs it once per
outstanding notice, every pass, with the notice as JSON in the **last argument** (never
through a shell). Exit 0 marks the notice delivered. Anything else leaves it outstanding,
records the attempt count and the command's stderr on the notice, and tries again next
pass — `python -m runtime.notify list` shows what is waiting and why the last send failed.
A notice's id is stable per (kind, run, step); the same fact is sent once, and sent again
only if it changes. While the command is unset the supervised scheduler warns at every start.

**The current operator inbox: GitHub issues.** The repository ships one sink,
`scripts/notify_github.py`, and it is the authoritative destination for MyOrg notices
until an incident channel exists:

```
MYORG_NOTIFY_COMMAND="python3 scripts/notify_github.py"
MYORG_NOTIFY_GITHUB_REPO="DipurajThapa/MyOrg"
```

Each notice becomes one issue titled `[MyOrg · <severity>] <subject>`, with the detail, the
action and the run/step in the body and the notice id in its last line. Closing the issue is
the acknowledgement. A notice that comes back changed reopens its issue and comments; a plain
retry adds nothing. `gh` must be able to authenticate as whoever runs the scheduler:

- *Windows task* (`deploy/install-scheduler-windows.ps1`): the task runs as the user who
  registered it, so that user's own `gh auth login` is used. Nothing more to set.
- *systemd* (`User=myorg`): that account has no `gh` login. Put a token in
  `/etc/myorg/myorg.env` as `GH_TOKEN` — a fine-grained token scoped to this repository with
  **Issues: read and write** and nothing else. Never in the repository, never in the unit file.

**Before wiring it for the first time**, run `python -m runtime.notify list`: every notice
raised while nothing was configured is still outstanding and will be sent the moment a
command exists — including ones about runs that have long since finished. Acknowledge the
stale ones with `python -m runtime.notify ack <id>` first, or expect them as issues.

**Test it:** `python -m runtime.notify test` sends one synthetic notice through the real
path and exits 1 (could not write the outbox), 2 (no command set), 3 (the command ran and
failed — its stderr is printed and kept on the notice), or 0 (the sink accepted it). Then
open the repository's issues and confirm you see it; that last step is yours.

**What it does and does not guarantee.** Delivered means GitHub stored the issue. It is an
inbox, not paging: no one is woken, and **GitHub does not notify a person of their own
actions** — an issue created by the same account that is meant to read it produces no
notification at all. For a notification to reach the operator, the token that creates
issues must belong to a different identity (a second account or a GitHub App) and the
operator must be watching the repository. Until that is true, the inbox works and the
alert does not.

The Prometheus rules in `deploy/prometheus-alerts.yml` are a second channel that watches the
same conditions from outside — but only if something scrapes `/metrics`.

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
worker that died (its claim expires on its own after `MYORG_CLAIM_SECONDS`, default 600, and
the driver adopts the step; `expire-claim` forces it sooner), a dependency that never completed, or an
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

## Stopping a run

A run that should not continue — wrong goal, duplicate, a trigger that fired on bad data —
is stopped by a person, not by waiting for a gate it may never reach:

`python -m runtime.company_runtime cancel-run <run> --approver <you> --reason "<why>"
--request-id <id>`, or `POST /v1/runs/<run>/cancel` with `{"reason": "..."}` as a
`decision-owner`.

It is terminal. Every finished step and its evidence stays; the audit log records who stopped
it and why. A step the agent was mid-way through is discarded when it returns — that attempt's
cost is the one figure the run's `spend_usd` will not include. To do the work again, start a
new run; the old one is the record of what was abandoned.

## Pausing the company

Two levers, both already there. Pick by how much you want to stop.

**Stop one source, stay in the console.** Pause a schedule in the Control Center or with
`PUT /v1/schedules/{id}/status` `{"enabled": false}`. A webhook trigger is paused the same
way it was registered — `POST /v1/triggers/webhook` with `"enabled": false`. Runs already
moving keep moving; cancel the ones you do not want (above).

**Stop everything.** `python -m runtime.admin organization-status --org <org> --status
suspended`. Suspended means the tenant is off: the scheduler starts nothing and drives
nothing, the webhook route refuses, the agent API offers and claims nothing, and every token
is refused — so the Control Center signs you out too. A step already dispatched when the
switch flips finishes and records its own result; the next step is not dispatched. Claims are
left as they are and simply wait — nothing is rolled back — and the run picks up where it
stopped on `--status active`. The watchers (projection, escalation, `/metrics`) keep running;
`myorg_org_suspended` reads 1 and `MyOrgOrganizationSuspended` fires after six hours so a
pause is never mistaken for a quiet day.

**Deciding steps while paused.** You cannot — suspension refuses your token. Cancel or
approve what needs it first, then suspend.

**Where decisions are made.** The Control Center, and nowhere else: steps, connector
approvals and memory proposals all bind to a registered human, a role, an organization and a
reason (`company/operating-principles.md` §9). The old loopback approvals console was
removed in 0.6.0. Without the Control Center, the CLI is the fallback — it needs shell
access, which is the trust boundary: `company_runtime approve|reject|cancel-run` and
`python -m runtime.memory approve|reject <id> --by <you>`.

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
