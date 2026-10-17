#!/bin/sh
set -eu
etcdctl() { /usr/local/bin/admin.sh "$@"; }
# The root client certificate bootstraps fresh stores and authenticates on repeats.
for attempt in $(seq 1 60); do
    if etcdctl endpoint status >/dev/null 2>&1; then break; fi
    if [ "$attempt" -eq 60 ]; then echo 'Etcd did not become ready' >&2; exit 1; fi
    sleep 1
done
etcdctl user get root >/dev/null 2>&1 || etcdctl user add root --no-password
etcdctl role get root >/dev/null 2>&1 || etcdctl role add root
etcdctl user grant-role root root
etcdctl role get patroni >/dev/null 2>&1 || etcdctl role add patroni
etcdctl role grant-permission patroni --prefix=true readwrite /service/postgres-lab/
if ! etcdctl user get patroni >/dev/null 2>&1; then
    etcdctl user add patroni --interactive=false < /run/secrets/etcd_patroni_password
fi
etcdctl user grant-role patroni patroni
etcdctl role get health >/dev/null 2>&1 || etcdctl role add health
etcdctl role grant-permission health --prefix=true read /health/
etcdctl user get etcd-health >/dev/null 2>&1 || etcdctl user add etcd-health --no-password
etcdctl user grant-role etcd-health health
etcdctl auth enable
etcdctl auth status
