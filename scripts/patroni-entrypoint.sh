#!/usr/bin/env bash

set -euo pipefail

export PATRONI_SUPERUSER_USERNAME="postgres"

PATRONI_SUPERUSER_PASSWORD="$(cat /run/secrets/postgres_password)"
export PATRONI_SUPERUSER_PASSWORD

export PATRONI_REPLICATION_USERNAME="replicator"

PATRONI_REPLICATION_PASSWORD="$(cat /run/secrets/replication_password)"
export PATRONI_REPLICATION_PASSWORD

install -d -o postgres -g postgres -m 700 /run/patroni
install -o postgres -g postgres -m 600 /run/secrets/patroni_key /run/patroni/node.key
install -o postgres -g postgres -m 644 /run/secrets/patroni_cert /run/patroni/node.crt
install -o postgres -g postgres -m 644 /run/secrets/patroni_ca /run/patroni/ca.crt
export PATRONI_RESTAPI_USERNAME=patroni-admin
PATRONI_RESTAPI_PASSWORD="$(cat /run/secrets/patroni_api_password)"
export PATRONI_RESTAPI_PASSWORD
export PATRONI_CTL_USERNAME=patroni-admin
export PATRONI_CTL_PASSWORD="$PATRONI_RESTAPI_PASSWORD"

install -o postgres -g postgres -m 644 /run/secrets/etcd_client_ca /run/patroni/etcd-ca.crt
install -o postgres -g postgres -m 644 /run/secrets/etcd_client_cert /run/patroni/etcd-client.crt
install -o postgres -g postgres -m 600 /run/secrets/etcd_client_key /run/patroni/etcd-client.key
export PATRONI_ETCD3_PROTOCOL=https
export PATRONI_ETCD3_USERNAME=patroni
PATRONI_ETCD3_PASSWORD="$(cat /run/secrets/etcd_patroni_password)"
export PATRONI_ETCD3_PASSWORD

exec runuser -u postgres -- \
    patroni /etc/patroni/patroni.yml
