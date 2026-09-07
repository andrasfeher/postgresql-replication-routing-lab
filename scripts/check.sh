#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

status=0

if command -v shellcheck >/dev/null 2>&1; then
    shellcheck scripts/*.sh pgbouncer/entrypoint.sh || status=$?
else
    echo "WARN: shellcheck not installed; skipping shell lint"
fi

docker compose config >/dev/null

docker run --rm \
    -v "$ROOT_DIR/haproxy/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro" \
    haproxy:3.2-alpine \
    haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg || status=$?

exit "$status"
