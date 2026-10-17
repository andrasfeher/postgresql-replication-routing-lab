#!/usr/bin/env bash
set -euo pipefail
export PATRONI_RESTAPI_USERNAME=patroni-admin
export PATRONI_CTL_USERNAME=patroni-admin
PATRONI_RESTAPI_PASSWORD="$(cat /run/secrets/patroni_api_password)"
export PATRONI_RESTAPI_PASSWORD
export PATRONI_CTL_PASSWORD="$PATRONI_RESTAPI_PASSWORD"
export PATRONI_ETCD3_PROTOCOL=https
export PATRONI_ETCD3_USERNAME=patroni
PATRONI_ETCD3_PASSWORD="$(cat /run/secrets/etcd_patroni_password)"
export PATRONI_ETCD3_PASSWORD
exec patronictl -c /etc/patroni/admin.yml "$@"
