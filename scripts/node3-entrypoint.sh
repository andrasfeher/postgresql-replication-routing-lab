#!/usr/bin/env bash
set -euo pipefail

PG_VERSION="${PG_VERSION:-18}"
CLUSTER="node3"

PGDATA="/var/lib/postgresql/${PG_VERSION}/${CLUSTER}"
CONFIG="/etc/postgresql/${PG_VERSION}/${CLUSTER}"

REPLICATION_PASSWORD_FILE="/run/secrets/replication_password"

[[ -s "$REPLICATION_PASSWORD_FILE" ]] || {
    echo "ERROR: replication password secret is missing or empty: $REPLICATION_PASSWORD_FILE" >&2
    exit 1
}

NEEDS_BASEBACKUP=false

#
# Create the Debian PostgreSQL configuration layout.
#
# Important:
# pg_createcluster also initializes a PostgreSQL data directory.
# We do NOT want that database on the standby. The standby must be
# initialized from the primary with pg_basebackup.
#
if [[ ! -d "$CONFIG" ]]; then
    echo "[node3] Creating Debian PostgreSQL cluster configuration"

    pg_createcluster \
        "$PG_VERSION" \
        "$CLUSTER" \
        --port=5432 \
        --start-conf=manual

    NEEDS_BASEBACKUP=true
fi

#
# PostgreSQL must listen on the Docker network.
#
sed -i -E \
    "s/^[#[:space:]]*listen_addresses[[:space:]]*=.*/listen_addresses = '*'/" \
    "$CONFIG/postgresql.conf"

#
# Allow client connections from the Docker network.
#
grep -qE \
    '^host[[:space:]]+all[[:space:]]+all[[:space:]]+samenet[[:space:]]+scram-sha-256' \
    "$CONFIG/pg_hba.conf" || \
    echo 'host all all samenet scram-sha-256' >> "$CONFIG/pg_hba.conf"

#
# If this is the first initialization, remove the temporary standalone
# database created by pg_createcluster.
#
if [[ "$NEEDS_BASEBACKUP" == true ]]; then
    echo "[node3] Removing temporary data directory created by pg_createcluster"

    rm -rf "${PGDATA:?}"
fi

#
# If no PostgreSQL data directory exists, initialize the standby from
# the primary.
#
if [[ ! -f "${PGDATA}/PG_VERSION" ]]; then
    echo "[node3] Initializing standby from primary"

    echo "[node3] Waiting for primary"

    until pg_isready -h pg-node1 -p 5432 >/dev/null 2>&1; do
        sleep 2
    done

    REPLICATION_PASSWORD="$(<"$REPLICATION_PASSWORD_FILE")"

    #
    # pg_basebackup uses libpq, so .pgpass prevents the password from
    # appearing in the command line.
    #
    install -d \
        -o postgres \
        -g postgres \
        -m 700 \
        /var/lib/postgresql

    printf '%s\n' \
        "pg-node1:5432:replication:replicator:${REPLICATION_PASSWORD}" \
        > /var/lib/postgresql/.pgpass

    chown postgres:postgres /var/lib/postgresql/.pgpass
    chmod 600 /var/lib/postgresql/.pgpass

    unset REPLICATION_PASSWORD

    echo "[node3] Running pg_basebackup"

    runuser -u postgres -- \
        pg_basebackup \
            -h pg-node1 \
            -p 5432 \
            -U replicator \
            -D "$PGDATA" \
            -Fp \
            -Xs \
            -P \
            -R \
            -v

    chown -R postgres:postgres "$PGDATA"

    echo "[node3] Base backup completed"
fi

#
# Safety guard:
# Never start this container unless PostgreSQL is explicitly configured
# as a standby.
#
if [[ ! -f "${PGDATA}/standby.signal" ]]; then
    echo "ERROR: standby.signal is missing; refusing to start as a writable server." >&2
    exit 1
fi

echo "[node3] Starting PostgreSQL standby"

exec runuser -u postgres -- \
    "/usr/lib/postgresql/${PG_VERSION}/bin/postgres" \
    -D "$PGDATA" \
    -c "config_file=${CONFIG}/postgresql.conf"