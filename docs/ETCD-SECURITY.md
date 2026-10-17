# Etcd security and administration

## Access model

Etcd members are attached only to the internal `coordination` network
(`172.30.81.0/24`), at `.11`, `.12`, and `.13`. Client port 2379 and peer port
2380 bind only to those addresses and use HTTPS. Neither port is published.
Only etcd, Patroni database nodes, the scoped Patroni administration service,
and the etcd administration/bootstrap services join this network. Application,
Mailpit, PgBouncer, HAProxy, subscriber, and Ansible services do not join it.
Docker administrators remain trusted because they can attach containers or read
secrets through the Docker daemon.

Both listeners require mutual TLS, with a minimum TLS version of 1.2. Separate
client and peer CAs prevent application client certificates from authenticating
as etcd peers. Peers also require the common name `etcd-peer`. Each member has
its own server and peer private keys, and each Patroni node has its own client
key. Certificate authorities' private keys remain on the host and are never
mounted into containers.

| Identity | Authentication | Permissions |
| --- | --- | --- |
| `patroni` | Client certificate plus username/password for Patroni's HTTP gateway | Read/write only `/service/postgres-lab/` |
| `etcd-health` | Native gRPC client certificate common name | Read only `/health/`; no Patroni data access |
| `root` | Native gRPC client certificate common name | Full etcd administration, available only to bootstrap and opt-in admin tools |

Patroni is not an etcd administrator. The `patroni-admin` service uses the same
scoped DCS role and a separate transport certificate; it does not receive the
root certificate or any peer keys. It accesses Patroni's separate HTTPS API for
administrative database operations.

## Fresh startup and persistent state

`make secrets` generates the password and PKI under ignored `secrets/` paths.
`etcd-bootstrap` waits for etcd, establishes users and roles, and enables auth.
PostgreSQL services require this job to complete successfully before starting.
Authentication and grants persist in etcd volumes. The bootstrap job is repeatable
and does not disable authentication or overwrite existing passwords.

The etcd HTTP gateway requires Patroni transport certificates to have an empty
common name. The gateway authenticates the explicit `patroni` username/password;
it cannot use certificate common names as user identities. Etcd server
certificates include both `serverAuth` and `clientAuth` because the gateway also
makes an internal TLS client connection. Patroni's entrypoints explicitly set
`PATRONI_ETCD3_PROTOCOL` so its environment parser loads the associated credentials.

## Administration

From the project root:

```bash
docker compose --profile admin run --rm --no-deps etcd-admin endpoint status --cluster -w table
docker compose --profile admin run --rm --no-deps etcd-admin endpoint health --cluster
docker compose --profile admin run --rm --no-deps etcd-admin auth status
docker compose --profile admin run --rm --no-deps etcd-admin role get patroni
docker compose --profile admin run --rm --no-deps patroni-admin list
```

The etcd admin wrapper supplies explicit TLS flags to avoid an etcdctl 3.6
conflict when environment-based flags are parsed repeatedly by `--cluster` commands.
Use `etcd-bootstrap` again to check initialization; do not use `auth disable` as
part of normal operation. Existing unencrypted etcd volumes require an intentional
membership/listener migration; changing Compose flags alone does not update
persisted peer URLs.

For a snapshot, create a private local destination first:

```bash
mkdir -p .tmp/backups
chmod 700 .tmp/backups
docker compose --profile admin run --rm --no-deps \
  -e ETCDCTL_ENDPOINTS=https://etcd1-dcs:2379 \
  -v "$PWD/.tmp/backups:/backup" etcd-admin snapshot save /backup/etcd-secured.db
```

Snapshots contain coordination state and password verifiers. Protect them together
with the PKI and password secrets. Backups and all secrets are excluded from all
four Docker build contexts through source allowlists.

## Verification

```bash
docker compose exec -T pg-node2 python3 < test/security/etcd_client.py
docker compose exec -T web python3 < test/security/etcd_isolation.py
docker compose exec -T web python3 < web/tests/permissions.py
docker compose exec -T web python3 < web/tests/smoke.py
```

The client test verifies mutual TLS, password authentication, namespace reads and
writes, and denial of out-of-scope reads/writes and user administration. Its test
key is deleted afterwards. The isolation test checks client and peer ports from
the web container. `test/security/etcd_peer_tls.py` additionally verifies that
peer certificates work while ordinary client certificates and missing certificates
are rejected; it runs in a disposable container sharing an etcd member network namespace (so
peer source-IP SAN verification remains enforced), with only
individual test certificate/key files mounted.

## Certificate and password rotation

Leaf certificates expire after 365 days and CAs after ten years. Generation
preserves existing keys/certificates and verifies certificate validity. Renew
using the matching CA, SANs, common-name rules, and extended usages; replace
individual files and roll the affected services. Patroni's etcd transport settings
and loaded certificates require a process restart. Keep enough healthy etcd
members online to retain quorum, and roll replicas before the database primary.
A password change requires updating the existing etcd user and the secret together;
bootstrap intentionally does not reset passwords on every startup.

## Migration record (2026-10-17)

A recovery snapshot was saved as `.tmp/backups/etcd-pre-security-20261017.db`.
The existing cluster was migrated through temporary dual listeners and peer URL
updates; the final deployment has no plaintext listeners or temporary ports.
Certificate compatibility issues caused a temporary loss of database leadership
and application availability during migration. Certificates and credential loading
were corrected, and service was restored. One replica encountered a checkpoint
recovery error. Its previous directory was preserved at
`/var/lib/postgresql/18/node1-before-etcd-recovery` in the `pg_node1_data` volume,
and it was rebuilt from the healthy primary. No data directory was deleted.
