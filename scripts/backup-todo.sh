#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
BACKUP_DIR="${ROOT_DIR}/.tmp/backups/todo-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
docker compose exec -T pg-node1 bash -c '
  export PGPASSWORD="$(cat /run/secrets/postgres_password)"
  exec pg_dump -h haproxy -U postgres -d todo --format=custom --create
' > "$BACKUP_DIR/todo.dump"
docker compose exec -T pg-node1 bash -c '
  export PGPASSWORD="$(cat /run/secrets/postgres_password)"
  exec pg_dumpall -h haproxy -U postgres -l todo --roles-only
' > "$BACKUP_DIR/roles.sql"
docker compose exec -T pg-node1 pg_restore --list < "$BACKUP_DIR/todo.dump" \
  > "$BACKUP_DIR/todo.dump.contents"
printf 'Backup created: %s\n' "$BACKUP_DIR"
