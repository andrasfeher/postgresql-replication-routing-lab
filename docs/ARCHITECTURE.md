# Architecture & design decisions

## Goal

The lab separates four concerns that are often collapsed into a single "PostgreSQL server" in small examples:

1. **database role** — primary vs. standby;
2. **replication** — physical WAL streaming;
3. **traffic routing** — HAProxy;
4. **connection management** — PgBouncer.

That separation makes it possible to reason about each layer independently and to test where a failure is handled — and where it is not.

## Request paths

### Read/write

```text
client :5432
  -> PgBouncer RW :6432
  -> HAProxy :5432
  -> pg-primary :5432
```

### Read-only

```text
client :5433
  -> PgBouncer RO :6432
  -> HAProxy :5433
  -> pg-secondary :5432
```

## PostgreSQL replication

The standby is initialized from the primary with `pg_basebackup -R`. `-R` writes the standby configuration needed to reconnect to the primary and creates `standby.signal`.

The project uses **asynchronous physical streaming replication**. Consequently, read-after-write consistency is not guaranteed on the RO endpoint and a primary failure can theoretically lose transactions that had not yet reached the standby.

The lab does not configure a permanent physical replication slot. This avoids unbounded WAL retention while a lab standby is offline, at the cost of requiring the standby to be rebuilt if it falls far enough behind that required WAL is no longer available.

## HAProxy

HAProxy provides two TCP frontends:

- `:5432` -> primary backend
- `:5433` -> standby backend

Health checks answer a narrow question: **is the configured PostgreSQL service reachable?** They do not elect a primary.

This distinction matters. If `pg-secondary` is manually promoted, the current HAProxy configuration does not automatically change the RW backend from `pg-primary` to `pg-secondary`.

## PgBouncer

Two PgBouncer instances provide stable client endpoints and use `transaction` pooling. The separation keeps the routing intent explicit and makes pool statistics independently observable for RW and RO paths.

Transaction pooling is efficient but changes session semantics. Applications that depend on session-level state, temporary objects across transactions, session advisory locks, or similar features need explicit compatibility testing.

## Authentication and secrets

PostgreSQL client/replication access uses SCRAM-SHA-256. Password files are generated locally under `secrets/` and ignored by Git. Docker mounts them under `/run/secrets/`.

PgBouncer creates its authentication file inside the container during startup rather than storing a plaintext `userlist.txt` in the repository.

## Persistence model

Data and Debian PostgreSQL configuration live in separate named volumes for each PostgreSQL node. This mirrors Debian's separation of `/var/lib/postgresql` and `/etc/postgresql` and makes the lab useful for learning `pg_createcluster`/`pg_ctlcluster` conventions.

Because those volume pairs form one logical node, reset them together with:

```bash
docker compose down -v
```

## What this lab is — and is not

### Demonstrates

- primary/standby PostgreSQL architecture;
- physical streaming replication;
- distinct read/write and read-only paths;
- health-checked TCP routing;
- transaction connection pooling;
- Docker networking, secrets, volumes and health checks.

### Does not implement

- consensus / leader election;
- automatic promotion;
- automatic HAProxy role discovery;
- fencing or split-brain protection;
- backups / PITR;
- TLS;
- monitoring/alerting stack;
- production resource limits and capacity planning.

Those omissions are intentional. They make the boundary between **replication** and a complete **HA system** explicit.

## Possible next iterations

1. Add pgBackRest and point-in-time recovery exercises.
2. Add Patroni + etcd/Consul and role-aware HAProxy checks.
3. Add Prometheus/Grafana exporters and dashboards.
4. Add TLS for client and replication traffic.
5. Add load generation and demonstrate PgBouncer pool behavior.
6. Rebuild the same architecture with Kubernetes/CloudNativePG and compare operational models.
