#!/usr/bin/env bash
set -euo pipefail
: "${MYORG_DB:?MYORG_DB is required}"
days="${MYORG_IDEMPOTENCY_RETENTION_DAYS:-30}"
case "$days" in
  ''|*[!0-9]*) echo "retention days must be numeric" >&2; exit 2 ;;
esac
if [ "$days" -lt 1 ] || [ "$days" -gt 365 ]; then
  echo "retention days must be 1..365" >&2
  exit 2
fi
python3 -m runtime.admin --db "$MYORG_DB" purge-transient --idempotency-days "$days"
python3 -m runtime.admin --db "$MYORG_DB" verify
