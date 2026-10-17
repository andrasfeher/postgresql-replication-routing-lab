#!/bin/sh
set -eu
umask 077

USERLIST="/etc/pgbouncer/userlist.txt"

write_user() {
    username="$1"
    password_file="$2"
    if [ ! -s "$password_file" ]; then
        echo "ERROR: password secret is missing or empty: $password_file" >&2
        exit 1
    fi
    # Quote passwords according to PgBouncer auth-file syntax.
    password="$(sed 's/"/""/g' "$password_file")"
    printf '"%s" "%s"\n' "$username" "$password" >> "$USERLIST"
    unset password
}

: > "$USERLIST"
chmod 600 "$USERLIST"
write_user postgres /run/secrets/postgres_password

# Only the RW service receives this secret; RO retains its existing users.
if [ -f /run/secrets/webapp_password ]; then
    write_user webapp /run/secrets/webapp_password
fi

echo "PgBouncer userlist generated."
exec pgbouncer /etc/pgbouncer/pgbouncer.ini
