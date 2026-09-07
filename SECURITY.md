# Security policy

This project is an educational local lab and is **not hardened for production**.

## Local-only defaults

Docker publishes PostgreSQL/PgBouncer and HAProxy statistics ports on `127.0.0.1`. Do not change those bindings to `0.0.0.0` without also designing appropriate firewalling, TLS, authentication and network controls.

## Secrets

Never commit:

- `secrets/postgres_password.txt`
- `secrets/replication_password.txt`
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
