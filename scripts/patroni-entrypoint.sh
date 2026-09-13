#!/usr/bin/env bash

set -euo pipefail

export PATRONI_SUPERUSER_USERNAME="postgres"

PATRONI_SUPERUSER_PASSWORD="$(cat /run/secrets/postgres_password)"
export PATRONI_SUPERUSER_PASSWORD

export PATRONI_REPLICATION_USERNAME="replicator"

PATRONI_REPLICATION_PASSWORD="$(cat /run/secrets/replication_password)"
export PATRONI_REPLICATION_PASSWORD

exec runuser -u postgres -- \
    patroni /etc/patroni/patroni.yml
