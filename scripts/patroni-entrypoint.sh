#!/usr/bin/env bash

set -euo pipefail

export PATRONI_SUPERUSER_USERNAME="postgres"
export PATRONI_SUPERUSER_PASSWORD="$(
    cat /run/secrets/postgres_password
)"

export PATRONI_REPLICATION_USERNAME="replicator"
export PATRONI_REPLICATION_PASSWORD="$(
    cat /run/secrets/replication_password
)"

exec runuser -u postgres -- \
    patroni /etc/patroni/patroni.yml