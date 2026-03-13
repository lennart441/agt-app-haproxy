#!/bin/bash
# Startet die HAProxy-Integrationstests in Docker.
#
# Usage:
#   ./scripts/run-haproxy-tests.sh                  # Standard (pytest -v --tb=short)
#   ./scripts/run-haproxy-tests.sh -k test_waf -v   # Nur WAF-Tests, verbose
#   PYTEST_ARGS="-x -v" ./scripts/run-haproxy-tests.sh  # Eigene Argumente
#
# Voraussetzungen: Docker (Compose v2), openssl.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/tests/haproxy/docker-compose.test.yaml"

# Temp-Verzeichnisse fuer Test-Artefakte (SSL-Cert, Maps-Kopie)
TMPDIR="$(mktemp -d)"
export TEST_SSL_DIR="$TMPDIR/ssl"
export TEST_MAP_DIR="$TMPDIR/maps"

cleanup() {
    echo ""
    echo "=== Aufraeumen ==="
    docker compose -f "$COMPOSE_FILE" down -v --remove-orphans 2>/dev/null || true
    rm -rf "$TMPDIR"
}
trap cleanup EXIT

# pytest-Argumente: CLI > PYTEST_ARGS env > Default
if [ $# -gt 0 ]; then
    export PYTEST_ARGS="$*"
elif [ -z "${PYTEST_ARGS:-}" ]; then
    export PYTEST_ARGS="-v --tb=short"
fi

# --- Self-signed Test-Zertifikat ---
mkdir -p "$TEST_SSL_DIR"
openssl req -x509 -newkey rsa:2048 \
    -keyout "$TEST_SSL_DIR/key.pem" -out "$TEST_SSL_DIR/cert.pem" \
    -days 1 -nodes -subj "/CN=localhost" 2>/dev/null
cat "$TEST_SSL_DIR/cert.pem" "$TEST_SSL_DIR/key.pem" > "$TEST_SSL_DIR/haproxy.pem"
rm -f "$TEST_SSL_DIR/key.pem" "$TEST_SSL_DIR/cert.pem"
echo "SSL-Zertifikat: $TEST_SSL_DIR/haproxy.pem"

# --- Maps-Kopie (Repo bleibt unberuehrt) ---
mkdir -p "$TEST_MAP_DIR"
cp "$REPO_ROOT/conf/maps/"* "$TEST_MAP_DIR/"
echo "Maps-Kopie:     $TEST_MAP_DIR/"

# --- Tests starten ---
echo ""
echo "=== HAProxy-Integrationstests starten ==="
echo "    pytest-Argumente: $PYTEST_ARGS"
echo ""

docker compose -f "$COMPOSE_FILE" up \
    --build \
    --abort-on-container-exit \
    --exit-code-from test-runner
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "=== Alle Tests bestanden ==="
else
    echo ""
    echo "=== Tests fehlgeschlagen (Exit-Code: $EXIT_CODE) ==="
fi

exit $EXIT_CODE
