#!/bin/sh
# Vorbereitung für lokalen Test: SSL-PEM + Socket-Verzeichnis (Rechte für HAProxy user 99:99).
# Produktion: echtes Zertifikat nach ssl/haproxy.pem, Socket-Volumen je nach Setup.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSL_DIR="$REPO_ROOT/ssl"
PEM="$SSL_DIR/haproxy.pem"
SOCKET_DIR="$REPO_ROOT/run/haproxy-stat"
HAPROXY_UID=99
HAPROXY_GID=99

mkdir -p "$SSL_DIR"
# Ein PEM: zuerst Zertifikat, dann privater Schlüssel (HAProxy-Format).
openssl req -x509 -newkey rsa:2048 \
  -keyout "$SSL_DIR/key.pem" -out "$SSL_DIR/cert.pem" \
  -days 365 -nodes \
  -subj "/CN=localhost"
cat "$SSL_DIR/cert.pem" "$SSL_DIR/key.pem" > "$PEM"
rm -f "$SSL_DIR/cert.pem" "$SSL_DIR/key.pem"
echo "Erstellt: $PEM (nur für lokalen Test)"

mkdir -p "$SOCKET_DIR"
chown ${HAPROXY_UID}:${HAPROXY_GID} "$SOCKET_DIR" 2>/dev/null || true
echo "Socket-Verzeichnis: $SOCKET_DIR (falls HAProxy abstürzt: sudo chown 99:99 $SOCKET_DIR)"
