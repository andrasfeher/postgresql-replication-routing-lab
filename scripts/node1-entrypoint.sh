#!/usr/bin/env bash
set -euo pipefail

PG_VERSION="${PG_VERSION:-18}"
CLUSTER="node1"
PGDATA="/var/lib/postgresql/${PG_VERSION}/${CLUSTER}"
CONFIG="/etc/postgresql/${PG_VERSION}/${CLUSTER}"
POSTGRES_PASSWORD_FILE="/run/secrets/postgres_password"
REPLICATION_PASSWORD_FILE="/run/secrets/replication_password"

require_file() {
    local file="$1"
    [[ -s "$file" ]] || { echo "ERROR: required secret is missing or empty: $file" >&2; exit 1; }
}

require_file "$POSTGRES_PASSWORD_FILE"
require_file "$REPLICATION_PASSWORD_FILE"

if [[ ! -d "$CONFIG" ]]; then
    echo "[node1] Creating Debian PostgreSQL cluster ${PG_VERSION}/${CLUSTER}"

    pg_createcluster \
        "$PG_VERSION" \
        "$CLUSTER" \
        --port=5432 \
        --start-conf=manual

    cat >> "${CONFIG}/postgresql.conf" <<'CONF'

# --- Lab replication settings ---
listen_addresses = '*'
wal_level = replica
max_wal_senders = 10
max_replication_slots = 10
hot_standby = on
CONF

    cat >> "${CONFIG}/pg_hba.conf" <<'HBA'

# Docker-network replication and client access (lab only).
host replication replicator samenet scram-sha-256
host all all samenet scram-sha-256
HBA

    echo "[node1] Starting PostgreSQL temporarily for role initialization"
    pg_ctlcluster "$PG_VERSION" "$CLUSTER" start

    POSTGRES_PASSWORD="$(<"$POSTGRES_PASSWORD_FILE")"
    REPLICATION_PASSWORD="$(<"$REPLICATION_PASSWORD_FILE")"

    # psql :'var' safely SQL-quotes the supplied value as a literal.
    runuser -u postgres -- psql \
        --set=ON_ERROR_STOP=1 \
        --set=postgres_password="$POSTGRES_PASSWORD" \
        --set=replication_password="$REPLICATION_PASSWORD" \
        --dbname=postgres <<'SQL'
ALTER ROLE postgres PASSWORD :'postgres_password';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'replicator') THEN
        CREATE ROLE replicator WITH REPLICATION LOGIN;
    END IF;
END
$$;
ALTER ROLE replicator PASSWORD :'replication_password';
SQL

    unset POSTGRES_PASSWORD REPLICATION_PASSWORD
    pg_ctlcluster "$PG_VERSION" "$CLUSTER" stop
elif [[ ! -f "${PGDATA}/PG_VERSION" ]]; then
    echo "ERROR: PostgreSQL config exists but node1 data directory is missing." >&2
    echo "       For this lab, reset all volumes together with: docker compose down -v" >&2
    exit 1
fi

echo "[node1] Starting PostgreSQL"
exec runuser -u postgres -- \
    "/usr/lib/postgresql/${PG_VERSION}/bin/postgres" \
    -D "$PGDATA" \
    -c "config_file=${CONFIG}/postgresql.conf"
