#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
umask 077
need(){ command -v "$1" >/dev/null 2>&1 || { echo "Brak polecenia: $1" >&2; exit 1; }; }
need openssl
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

OUT_DIR="${OUT_DIR:-$PROJECT_ROOT/pki}"
AGENT_NAME="${AGENT_NAME:-camera}"
AGENT_IP="${AGENT_IP:-192.168.1.2}"
CLIENT_NAME="${CLIENT_NAME:-camera-compute}"
DAYS_CA="${DAYS_CA:-3650}"
DAYS_LEAF="${DAYS_LEAF:-825}"
mkdir -p "$OUT_DIR"
[[ ! -e "$OUT_DIR/ca.key" ]] || { echo "PKI już istnieje w $OUT_DIR; użyj innego OUT_DIR" >&2; exit 1; }

openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$OUT_DIR/ca.key"
openssl req -x509 -new -sha256 -key "$OUT_DIR/ca.key" -days "$DAYS_CA" \
  -subj "/CN=Camera Entropy Local CA" -out "$OUT_DIR/ca.crt"

cat > "$OUT_DIR/agent.ext" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:${AGENT_NAME}${AGENT_IP:+,IP:${AGENT_IP}}
EOF
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$OUT_DIR/agent.key"
openssl req -new -sha256 -key "$OUT_DIR/agent.key" -subj "/CN=${AGENT_NAME}" -out "$OUT_DIR/agent.csr"
openssl x509 -req -sha256 -in "$OUT_DIR/agent.csr" -CA "$OUT_DIR/ca.crt" -CAkey "$OUT_DIR/ca.key" \
  -CAcreateserial -days "$DAYS_LEAF" -extfile "$OUT_DIR/agent.ext" -out "$OUT_DIR/agent.crt"

cat > "$OUT_DIR/client.ext" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=clientAuth
EOF
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$OUT_DIR/client.key"
openssl req -new -sha256 -key "$OUT_DIR/client.key" -subj "/CN=${CLIENT_NAME}" -out "$OUT_DIR/client.csr"
openssl x509 -req -sha256 -in "$OUT_DIR/client.csr" -CA "$OUT_DIR/ca.crt" -CAkey "$OUT_DIR/ca.key" \
  -CAcreateserial -days "$DAYS_LEAF" -extfile "$OUT_DIR/client.ext" -out "$OUT_DIR/client.crt"

rm -f "$OUT_DIR"/*.csr "$OUT_DIR"/*.ext "$OUT_DIR"/*.srl
chmod 600 "$OUT_DIR"/*.key
chmod 644 "$OUT_DIR"/*.crt
openssl verify -CAfile "$OUT_DIR/ca.crt" "$OUT_DIR/agent.crt" "$OUT_DIR/client.crt"
cat <<EOF
Gotowe: $OUT_DIR

Host USB:
  ca.crt
  agent.crt
  agent.key

Serwer obliczeniowy:
  ca.crt
  client.crt
  client.key

TLS_SERVER_NAME=${AGENT_NAME}
ALLOWED_CLIENT_CN=${CLIENT_NAME}
EOF
