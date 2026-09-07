#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="${ROOT_DIR}/secrets"
mkdir -p "$SECRETS_DIR"

if ! command -v openssl >/dev/null 2>&1; then
    echo "ERROR: openssl is required to generate lab passwords." >&2
    exit 1
fi

create_secret() {
    local file="$1"
    if [[ -e "$file" ]]; then
        echo "Keeping existing secret: ${file#$ROOT_DIR/}"
    else
        umask 077
        openssl rand -base64 24 | tr -d '\n' > "$file"
        printf '\n' >> "$file"
        echo "Created: ${file#$ROOT_DIR/}"
    fi
}

create_secret "$SECRETS_DIR/postgres_password.txt"
create_secret "$SECRETS_DIR/replication_password.txt"
