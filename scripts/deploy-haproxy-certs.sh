#!/bin/bash
#
# Einmal täglich per Cron ausführen: Erneuert Let's Encrypt-Zertifikate (certbot renew)
# und deployt sie auf alle HAProxy-Knoten inkl. hitless Reload.
# Läuft auf EINEM Server (z. B. AGT1). Konfiguration: scripts/cert-deploy.env (siehe cert-deploy.env.example).
#
# Kritische Infrastruktur: Bei Fehlern wird abgebrochen; pro Knoten werden
# Config-Check und Health nach Reload geprüft.
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Konfiguration laden (optional)
if [ -f "$SCRIPT_DIR/cert-deploy.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/cert-deploy.env"
    set +a
fi

# Defaults
DOMAIN="${CERT_DOMAIN:-}"
LE_DIR="${CERT_LE_DIR:-}"
SERVERS="${CERT_SERVERS:-}"
SSH_USER="${CERT_SSH_USER:-root}"
SSH_KEY="${CERT_SSH_KEY:-}"
TARGET_DIR="${CERT_TARGET_DIR:-}"
CONTAINER_NAME="${CERT_CONTAINER_NAME:-haproxy_gateway}"
ADMIN_EMAIL="${CERT_ADMIN_EMAIL:-}"

LOG_FILE="${LOG_FILE:-/tmp/haproxy_cert_deploy.log}"
STATE_FILE="${STATE_FILE:-/var/log/haproxy_cert_last_md5.txt}"
TMP_CERT="${TMP_CERT:-/tmp/haproxy.pem}"

# SSH-Key: falls nicht gesetzt, typische Keys probieren
if [ -z "$SSH_KEY" ]; then
    for k in "$HOME/.ssh/id_ed25519" "$HOME/.ssh/id_rsa"; do
        if [ -f "$k" ]; then
            SSH_KEY="$k"
            break
        fi
    done
fi

# ==========================================
# Validierung der Konfiguration
# ==========================================
if [ -z "$DOMAIN" ] || [ -z "$LE_DIR" ] || [ -z "$SERVERS" ] || [ -z "$TARGET_DIR" ]; then
    echo "FEHLER: Konfiguration unvollständig. Bitte scripts/cert-deploy.env anlegen (Vorlage: cert-deploy.env.example)."
    echo "Erforderlich: CERT_DOMAIN, CERT_LE_DIR, CERT_SERVERS, CERT_TARGET_DIR"
    exit 1
fi

if [ -z "$SSH_KEY" ] || [ ! -f "$SSH_KEY" ]; then
    echo "FEHLER: SSH-Key nicht gefunden. CERT_SSH_KEY setzen oder id_ed25519/id_rsa unter \$HOME/.ssh anlegen."
    exit 1
fi

# ==========================================
# Initialisierung & Logging
# ==========================================
: > "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starte Zertifikats-Deployment für $DOMAIN"

# ==========================================
# 0. Certbot: Zertifikate erneuern (falls fällig)
# ==========================================
if command -v certbot >/dev/null 2>&1; then
    echo "[INFO] Führe certbot renew aus..."
    certbot renew --quiet || { echo "[FEHLER] certbot renew fehlgeschlagen."; exit 1; }
else
    echo "[WARN] certbot nicht gefunden – überspringe Erneuerung, deploye vorhandene Zertifikate."
fi

# ==========================================
# 1. Prüfen, ob ein neues Zertifikat vorliegt
# ==========================================
if [ ! -f "$LE_DIR/fullchain.pem" ] || [ ! -f "$LE_DIR/privkey.pem" ]; then
    echo "FEHLER: Let's Encrypt Zertifikate nicht gefunden in $LE_DIR"
    if [ -n "$ADMIN_EMAIL" ] && command -v mail >/dev/null 2>&1; then
        mail -s "FEHLER: HAProxy Zertifikat Deployment" "$ADMIN_EMAIL" < "$LOG_FILE" || true
    fi
    exit 1
fi

CURRENT_MD5=$(md5sum "$LE_DIR/fullchain.pem" | awk '{print $1}')

if [ -f "$STATE_FILE" ]; then
    LAST_MD5=$(cat "$STATE_FILE")
    if [ "$CURRENT_MD5" = "$LAST_MD5" ]; then
        echo "[INFO] Zertifikat hat sich nicht geändert. Kein Deployment notwendig."
        exit 0
    fi
fi

echo "[INFO] Neues Zertifikat erkannt. Bereite Deployment vor..."

# ==========================================
# 2. Zertifikat für HAProxy formatieren
# ==========================================
# HAProxy erwartet Fullchain und Private Key in einer Datei
cat "$LE_DIR/fullchain.pem" "$LE_DIR/privkey.pem" > "$TMP_CERT"
chmod 600 "$TMP_CERT"

# ==========================================
# 3. Server sequenziell updaten
# ==========================================
IFS=',' read -ra SERVER_ARRAY <<< "$SERVERS"

for SERVER in "${SERVER_ARRAY[@]}"; do
    SERVER=$(echo "$SERVER" | tr -d ' ')
    [ -z "$SERVER" ] && continue

    echo "---------------------------------------------------"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Verarbeite Server: $SERVER"

    # 3.1 Zertifikat kopieren
    echo "  -> Kopiere Zertifikat..."
    if ! scp -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$TMP_CERT" "${SSH_USER}@${SERVER}:${TARGET_DIR}/haproxy.pem"; then
        echo "  [FEHLER] SCP fehlgeschlagen auf $SERVER!"
        if [ -n "$ADMIN_EMAIL" ] && command -v mail >/dev/null 2>&1; then
            mail -s "CRITICAL: HAProxy Cert Deployment fehlgeschlagen auf $SERVER" "$ADMIN_EMAIL" < "$LOG_FILE" || true
        fi
        exit 1
    fi

    # 3.2 HAProxy Config Check im Container
    echo "  -> Prüfe HAProxy Konfiguration im Container..."
    if ! ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${SSH_USER}@${SERVER}" "docker exec $CONTAINER_NAME haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg"; then
        echo "  [FEHLER] Config Check fehlgeschlagen auf $SERVER! Zertifikat ungültig oder Config fehlerhaft."
        if [ -n "$ADMIN_EMAIL" ] && command -v mail >/dev/null 2>&1; then
            mail -s "CRITICAL: HAProxy Config Check fehlgeschlagen auf $SERVER" "$ADMIN_EMAIL" < "$LOG_FILE" || true
        fi
        exit 1
    fi

    # 3.3 Hitless Reload (SIGUSR2)
    echo "  -> Führe hitless Reload durch..."
    if ! ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${SSH_USER}@${SERVER}" "docker kill -s USR2 $CONTAINER_NAME"; then
        echo "  [FEHLER] Reload-Befehl fehlgeschlagen auf $SERVER!"
        if [ -n "$ADMIN_EMAIL" ] && command -v mail >/dev/null 2>&1; then
            mail -s "CRITICAL: HAProxy Reload fehlgeschlagen auf $SERVER" "$ADMIN_EMAIL" < "$LOG_FILE" || true
        fi
        exit 1
    fi

    # 3.4 Warten und Health-Status prüfen
    echo "  -> Warte 15 Sekunden auf den Docker Healthcheck..."
    sleep 15

    HEALTH=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${SSH_USER}@${SERVER}" "docker inspect --format='{{json .State.Health.Status}}' $CONTAINER_NAME 2>/dev/null | tr -d '\"'")
    if [ -z "$HEALTH" ]; then
        # Ältere Docker-Versionen: Health kann fehlen, dann State.Status prüfen
        HEALTH=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${SSH_USER}@${SERVER}" "docker inspect --format='{{.State.Status}}' $CONTAINER_NAME 2>/dev/null" || echo "unknown")
    fi

    if [ "$HEALTH" != "healthy" ] && [ "$HEALTH" != "running" ]; then
        echo "  [FEHLER] HAProxy Container auf $SERVER ist nicht healthy! Aktueller Status: $HEALTH"
        if [ -n "$ADMIN_EMAIL" ] && command -v mail >/dev/null 2>&1; then
            mail -s "CRITICAL: HAProxy Container nach Reload auf $SERVER: $HEALTH" "$ADMIN_EMAIL" < "$LOG_FILE" || true
        fi
        exit 1
    fi

    echo "  [OK] Server $SERVER erfolgreich aktualisiert (Status: $HEALTH)."
done

# ==========================================
# 4. Aufräumen & Abschluss
# ==========================================
rm -f "$TMP_CERT"
echo "$CURRENT_MD5" > "$STATE_FILE"

echo "---------------------------------------------------"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Deployment auf allen Servern erfolgreich abgeschlossen."

if [ -n "$ADMIN_EMAIL" ] && command -v mail >/dev/null 2>&1; then
    mail -s "SUCCESS: HAProxy Zertifikate erneuert ($DOMAIN)" "$ADMIN_EMAIL" < "$LOG_FILE" || true
fi

exit 0
