# Troubleshooting

## Containers do not become healthy

Start with:

```bash
docker compose ps
docker compose logs pg-primary
docker compose logs pg-secondary
docker compose logs haproxy
docker compose logs pgbouncer-rw
docker compose logs pgbouncer-ro
```

## Authentication fails

Regenerate a clean lab if you changed secret files after PostgreSQL was initialized. PostgreSQL role passwords are set during first initialization and changing the Docker secret later does not automatically alter the database role.

```bash
make reset
rm -f secrets/postgres_password.txt secrets/replication_password.txt
make secrets
make up
```

Then verify:

```bash
make verify
```

## Standby is not streaming

On the primary:

```bash
docker compose exec pg-primary \
  runuser -u postgres -- psql -x -c "SELECT * FROM pg_stat_replication;"
```

On the standby:

```bash
docker compose exec pg-secondary \
  runuser -u postgres -- psql -x -c "SELECT * FROM pg_stat_wal_receiver;"
```

Also inspect:

```bash
docker compose logs pg-secondary
```

## RW endpoint connects to the wrong role

```bash
export PGPASSWORD="$(cat secrets/postgres_password.txt)"
psql -h 127.0.0.1 -p 5432 -U postgres -d postgres \
  -c "SELECT pg_is_in_recovery();"
```

Expected: `false`.

## RO endpoint connects to the wrong role

```bash
psql -h 127.0.0.1 -p 5433 -U postgres -d postgres \
  -c "SELECT pg_is_in_recovery();"
```

Expected: `true`.

## PgBouncer diagnostics

```bash
psql -h 127.0.0.1 -p 5432 -U postgres -d pgbouncer -c "SHOW POOLS;"
psql -h 127.0.0.1 -p 5432 -U postgres -d pgbouncer -c "SHOW SERVERS;"
psql -h 127.0.0.1 -p 5432 -U postgres -d pgbouncer -c "SHOW STATS;"
```

## HAProxy diagnostics

Validate the config without starting the lab:

```bash
docker run --rm \
  -v "$PWD/haproxy/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro" \
  haproxy:3.2-alpine \
  haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg
```

The stats page is available at `http://127.0.0.1:8404/stats` while the lab is running.

## Config volume exists but data volume does not

The primary intentionally refuses to start when its Debian config volume exists while its data directory is missing. Reset both together:

```bash
make reset
make up
```
