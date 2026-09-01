# Deployment examples

These files are reviewed examples, not executed deployment evidence. The API defaults to
loopback and refuses a non-loopback bind unless the operator asserts a TLS-terminating boundary.

Provision a dedicated `myorg` OS account, `/opt/myorg` application directory,
`/var/lib/myorg` state/backup directory, and `/etc/myorg/myorg.env` mode `0600`. Inject auth,
gateway and metrics secrets from an approved manager; do not copy them into
`myorg.env.example`. Bind platform subjects to actors only after joiner/mover/leaver approval.

Install/enable `myorg-api.service`, `myorg-backup.timer`, and `myorg-maintenance.timer`. Configure
the UI worker with the HTTPS API URL and the same gateway secret. Restrict `/metrics` to the
monitoring network and bearer token, then load `prometheus-alerts.yml` with the runbook URL.

Before starting: `python -m runtime.admin --db "$MYORG_DB" verify`. After starting: validate
health/readiness, signed UI identity, negative authorization, tenant isolation, metrics/log sinks,
alert routing and a verified backup. Generate the source/SBOM/scan bundle and require a completed
release gate record. Follow `docs/UAT-DEPLOYMENT-AND-ROLLBACK.md`; a human owns OAuth, go-live,
restore and rollback.
