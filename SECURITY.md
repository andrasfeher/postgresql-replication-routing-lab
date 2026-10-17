# Security policy

This project is an educational local lab and is **not hardened for production**.

## Local-only defaults

Docker publishes PostgreSQL/PgBouncer and HAProxy statistics ports on `127.0.0.1`. Do not change those bindings to `0.0.0.0` without also designing appropriate firewalling, TLS, authentication and network controls.

## Secrets

Never commit:

- `secrets/postgres_password.txt`
- `secrets/replication_password.txt`
- `secrets/webapp_password.txt`
- `secrets/patroni_api_password.txt`
- `secrets/etcd_patroni_password.txt`
- `secrets/etcd-tls/` private keys
- `secrets/patroni-tls/` private keys
- PgBouncer `userlist.txt` files containing passwords
- `.pgpass` files
- production hostnames, credentials, certificates or private keys

The supplied `.gitignore` and `.dockerignore` reduce accidental exposure but are not substitutes for reviewing commits before pushing.

## Production gaps

Before adapting this design to a real environment, evaluate at minimum:

- TLS in transit;
- secrets management / rotation;
- least-privilege database roles;
- host/network isolation;
- automated failover with split-brain protection;
- backups and point-in-time recovery;
- monitoring, alerting and audit requirements;
- resource limits and capacity planning;
- image provenance, vulnerability scanning and patching.

## Patroni management

Patroni uses HTTPS on the internal `patroni_mgmt` network and does not listen on
its application-network address. Unsafe methods require both the administrative
password and a source address on the allowlist (the dedicated admin container or
registered members). HAProxy verifies the management CA and per-node hostname;
it has no administrative credentials and is excluded from unsafe API access.
Use the opt-in `patroni-admin` Compose service for administration. The local CA
private key is never mounted in containers. Protect and renew the certificates
as described in README.md. Existing Docker socket administrators remain trusted.
PostgreSQL transport remains a separate hardening item. Etcd is protected as
described below.

## Etcd coordination

Etcd requires client and peer mutual TLS using separate CAs. Its listeners bind
only to the internal coordination network. Patroni's password-authenticated role
has read/write access only to `/service/postgres-lab/`; health checks have only
read access to `/health/`. Root certificates are restricted to bootstrap and
opt-in administration tools. PostgreSQL startup waits for authentication bootstrap.
See `docs/ETCD-SECURITY.md` for operations and rotation requirements.

All build contexts use source allowlists; backups, private keys, passwords, and
unrelated local files are excluded by default.

## Web frontend

Only NGINX publishes the application port, bound to `127.0.0.1:8080`. It joins a
separate edge bridge and an internal frontend network, with no database or
coordination network membership and no database credentials. The proxy runs
unprivileged with a read-only filesystem, buffers and limits incoming bodies,
and applies connection/rate limits and idle timeouts. The Python backend has
bounded request threads and socket idle timeouts, and trusts client-IP headers
only from the configured proxy address. Verification query strings are omitted
from access logs; error logs are suppressed for the verification endpoint.
This local frontend still uses HTTP; HTTPS and Secure cookies remain open items.
