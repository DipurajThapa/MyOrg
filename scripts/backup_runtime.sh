#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${MYORG_DB:?MYORG_DB is required}"
: "${MYORG_BACKUP_DIR:?MYORG_BACKUP_DIR is required}"
case "$MYORG_BACKUP_DIR" in
  /var/lib/myorg/backups|/srv/myorg/backups) ;;
  *) echo "refusing unapproved backup directory" >&2; exit 2 ;;
esac
mkdir -p -- "$MYORG_BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="$MYORG_BACKUP_DIR/myorg-$stamp.db"
python3 -m runtime.admin --db "$MYORG_DB" backup --output "$destination"
python3 -m runtime.admin --db "$destination" verify
echo "backup verified: $destination"
