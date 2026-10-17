#!/bin/sh
set -eu
endpoints="${ETCDCTL_ENDPOINTS:-https://etcd1-dcs:2379,https://etcd2-dcs:2379,https://etcd3-dcs:2379}"
# etcdctl 3.6 may parse flags twice for --cluster commands. Use explicit flags
# instead of environment-backed TLS flags to avoid a shadowing conflict.
unset ETCDCTL_ENDPOINTS ETCDCTL_CACERT ETCDCTL_CERT ETCDCTL_KEY ETCDCTL_COMMAND_TIMEOUT
exec /usr/local/bin/etcdctl --endpoints="$endpoints" \
    --cacert=/run/secrets/etcd_client_ca --cert=/run/secrets/etcd_root_cert \
    --key=/run/secrets/etcd_root_key --command-timeout=5s "$@"
