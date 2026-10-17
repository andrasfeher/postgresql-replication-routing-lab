#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TLS_DIR="$ROOT_DIR/secrets/etcd-tls"
mkdir -p "$TLS_DIR"
for ca in client peer; do
    if [[ ! -e "$TLS_DIR/$ca-ca.key" && ! -e "$TLS_DIR/$ca-ca.crt" ]]; then
        openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 3650 \
            -subj "/CN=Etcd Lab $ca CA" -addext 'basicConstraints=critical,CA:TRUE' \
            -addext 'keyUsage=critical,keyCertSign,cRLSign' \
            -keyout "$TLS_DIR/$ca-ca.key" -out "$TLS_DIR/$ca-ca.crt" 2>/dev/null
    fi
    [[ -s "$TLS_DIR/$ca-ca.key" && -s "$TLS_DIR/$ca-ca.crt" ]] || { echo "Incomplete $ca CA" >&2; exit 1; }
    chmod 644 "$TLS_DIR/$ca-ca.crt"
done
issue() {
    local name="$1" ca="$2" cn="$3" usage="$4" san="$5"
    local prefix="$TLS_DIR/$name"
    local subject="/CN=$cn"
    [[ "$cn" != none ]] || subject="/O=Patroni-DCS"
    if [[ -e "$prefix.key" || -e "$prefix.crt" ]]; then
        [[ -s "$prefix.key" && -s "$prefix.crt" ]] || { echo "Incomplete certificate pair: $name" >&2; exit 1; }
        openssl verify -CAfile "$TLS_DIR/$ca-ca.crt" "$prefix.crt" >/dev/null
        return
    fi
    openssl req -new -newkey rsa:3072 -nodes -sha256 -subj "$subject" \
        -keyout "$prefix.key" -out "$prefix.csr" 2>/dev/null
    printf '%s\n' 'basicConstraints=critical,CA:FALSE' \
        'keyUsage=critical,digitalSignature,keyEncipherment' \
        "extendedKeyUsage=$usage" "subjectAltName=$san" > "$prefix.ext"
    openssl x509 -req -sha256 -days 365 -in "$prefix.csr" \
        -CA "$TLS_DIR/$ca-ca.crt" -CAkey "$TLS_DIR/$ca-ca.key" -CAcreateserial \
        -extfile "$prefix.ext" -out "$prefix.crt" 2>/dev/null
    rm "$prefix.csr" "$prefix.ext"
}
for n in 1 2 3; do
    issue "etcd$n-server" client "etcd$n-dcs" serverAuth,clientAuth "DNS:etcd$n-dcs,IP:172.30.81.$((10+n))"
    issue "etcd$n-peer" peer etcd-peer serverAuth,clientAuth "DNS:etcd$n-dcs,IP:172.30.81.$((10+n))"
    issue "patroni$n" client none clientAuth "DNS:pg-node$n-dcs"
done
issue root client root clientAuth DNS:etcd-admin
issue patroni-admin client none clientAuth DNS:patroni-admin
issue health client etcd-health clientAuth DNS:etcd-health
printf 'Etcd client/peer PKI ready; CA keys remain local.\n'
