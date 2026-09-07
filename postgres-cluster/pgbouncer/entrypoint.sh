#!/bin/sh
set -eu

PASSWORD_FILE="/run/secrets/postgres_password"
USERLIST="/etc/pgbouncer/userlist.txt"

if [ ! -f "$PASSWORD_FILE" ]; then
    echo "ERROR: PostgreSQL password secret is missing: $PASSWORD_FILE" >&2
    exit 1
fi

PASSWORD="$(cat "$PASSWORD_FILE")"

printf '"postgres" "%s"\n' "$PASSWORD" > "$USERLIST"

chmod 600 "$USERLIST"

unset PASSWORD

echo "PgBouncer userlist generated."

exec pgbouncer /etc/pgbouncer/pgbouncer.ini