#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Validate with the deployed filesystem/user constraints, without taking the
# live proxy's fixed IP or joining any project networks.
docker run --rm --network none --user 101:101 --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=32m,mode=1777 \
    --cap-drop ALL --security-opt no-new-privileges:true \
    -v "$ROOT_DIR/nginx:/etc/nginx:ro" --entrypoint nginx nginx:stable-alpine -t
