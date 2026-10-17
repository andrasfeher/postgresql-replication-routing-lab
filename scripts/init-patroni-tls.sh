#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TLS_DIR="$ROOT_DIR/secrets/patroni-tls"
mkdir -p "$TLS_DIR"
if [[ ! -s "$TLS_DIR/ca.key" && ! -s "$TLS_DIR/ca.crt" ]]; then
    openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 3650 \
        -subj '/CN=Patroni Lab Management CA' \
        -addext 'basicConstraints=critical,CA:TRUE' \
        -addext 'keyUsage=critical,keyCertSign,cRLSign' \
        -keyout "$TLS_DIR/ca.key" -out "$TLS_DIR/ca.crt" 2>/dev/null
fi
[[ -s "$TLS_DIR/ca.key" && -s "$TLS_DIR/ca.crt" ]] || {
    echo 'ERROR: incomplete Patroni CA; restore the matching CA key and certificate.' >&2
    exit 1
}
for node in 1 2 3; do
    prefix="$TLS_DIR/pg-node$node"
    if [[ -e "$prefix.key" || -e "$prefix.crt" ]]; then
        [[ -s "$prefix.key" && -s "$prefix.crt" ]] || { echo "Incomplete certificate pair: $prefix" >&2; exit 1; }
        openssl verify -CAfile "$TLS_DIR/ca.crt" "$prefix.crt" >/dev/null
        continue
    fi
    openssl req -new -newkey rsa:3072 -nodes -sha256 \
        -subj "/CN=pg-node$node-mgmt" \
        -keyout "$prefix.key" -out "$prefix.csr" 2>/dev/null
    printf '%s\n' 'basicConstraints=critical,CA:FALSE' \
        'keyUsage=critical,digitalSignature,keyEncipherment' \
        'extendedKeyUsage=serverAuth' \
        "subjectAltName=DNS:pg-node$node-mgmt,IP:172.30.80.$((10 + node))" > "$prefix.ext"
    openssl x509 -req -sha256 -days 365 -in "$prefix.csr" \
        -CA "$TLS_DIR/ca.crt" -CAkey "$TLS_DIR/ca.key" -CAcreateserial \
        -extfile "$prefix.ext" -out "$prefix.crt" 2>/dev/null
    rm "$prefix.csr" "$prefix.ext"
done
chmod 644 "$TLS_DIR/ca.crt"
printf 'Patroni TLS certificates ready. CA private key remains local.\n'
