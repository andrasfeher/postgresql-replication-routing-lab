#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PASSWORD_FILE="secrets/postgres_password.txt"
[[ -s "$PASSWORD_FILE" ]] || { echo "ERROR: run ./scripts/init-secrets.sh first" >&2; exit 1; }
PASSWORD="$(<"$PASSWORD_FILE")"

printf '\n== Container status ==\n'
docker compose ps

printf '\n== node1 role ==\n'
docker compose exec -T pg-node1 \
    runuser -u postgres -- psql -XAtc "SELECT 'in_recovery=' || pg_is_in_recovery();"

printf '\n== node2 role ==\n'
docker compose exec -T pg-node2 \
    runuser -u postgres -- psql -XAtc "SELECT 'in_recovery=' || pg_is_in_recovery();"

printf '\n== node3 role ==\n'
docker compose exec -T pg-node3 \
    runuser -u postgres -- psql -XAtc "SELECT 'in_recovery=' || pg_is_in_recovery();"

printf '\n== Streaming replication ==\n'
docker compose exec -T pg-node1 \
    runuser -u postgres -- psql -X -c \
    "SELECT application_name, state, sync_state, client_addr FROM pg_stat_replication;"

printf '\n== Read/write endpoint (:5432 via PgBouncer -> HAProxy -> node1) ==\n'
PGPASSWORD="$PASSWORD" psql -X -h 127.0.0.1 -p 5432 -U postgres -d postgres -Atc \
    "SELECT 'recovery=' || pg_is_in_recovery() || ', port=' || inet_server_port();"

printf '\n== Read-only endpoint (:5433 via PgBouncer -> HAProxy -> standby) ==\n'
PGPASSWORD="$PASSWORD" psql -X -h 127.0.0.1 -p 5433 -U postgres -d postgres -Atc \
    "SELECT 'recovery=' || pg_is_in_recovery() || ', port=' || inet_server_port();"

printf '\n== Read-only endpoint (:5434 via PgBouncer -> HAProxy -> standby) ==\n'
PGPASSWORD="$PASSWORD" psql -X -h 127.0.0.1 -p 5434 -U postgres -d postgres -Atc \
    "SELECT 'recovery=' || pg_is_in_recovery() || ', port=' || inet_server_port();"

unset PASSWORD
printf '\nVerification complete.\n'
