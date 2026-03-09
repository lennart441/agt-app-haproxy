
#confidential 
erstellt am 20.02.2026
zuletzt verändert am 20.02.2026
used debian 13
## Grundkonfiguration 
auf allen servern
```bash
# updates installieren
sudo apt update
sudo apt upgrade

# reboot für kernel update 
reboot

# installation der basis anwendungen und grundeinstellungen
sudo apt install wireguard 
sudo timedatectl set-timezone Europe/Berlin
sudo apt install fail2ban
sudo systemctl enable fail2ban --now

#########################################################
# installation von docker
# Add Docker's official GPG key:
sudo apt update
sudo apt install ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the repository to Apt sources:
sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: $(. /etc/os-release && echo "$VERSION_CODENAME")
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF
#########################################################
sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo docker run hello-world

```

Wireguard installieren
```
# wireguard installation
# WG config (keys generieren)
wg genkey | tee privatekey | wg pubkey > publickey

# shared keys
wg genpsk > psk_agt1_agt2.key
wg genpsk > psk_agt1_agt3.key
wg genpsk > psk_agt2_agt3.key

# jweils für die server:
sudo nano /etc/wireguard/wg0.conf

```

Nur auf einem Server (AGT-1)
```
### Nur auf einem Server (AGT1) ###
# Zertifikate
#Install Certbot
apt install nano certbot -y

#Install Certbot DNS Cloudflare Package
apt install python3-certbot-dns-cloudflare

#Configure Certbot DNS Plugin
nano ~/certbot-creds.ini

#Paste Clodflare DNS Token
# Cloudflare API token used by Certbot
dns_cloudflare_api_token = 0123456789abcdef0123456789abcdef01234567

#secure Cloudflare DNS Token file
chmod 600 ~/certbot-creds.ini

#get certificate
certbot certonly \
  --dns-cloudflare \
  --dns-cloudflare-credentials /root/certbot-creds.ini \
  --dns-cloudflare-propagation-seconds 60 \
  -d ljoswig.de \
  -d *.ljoswig.de

#list certificates
certbot certificates

# erstellen von ssh keys
# kein pfad angeben, kein passwort eingeben, kein passwort eingeben
ssh-keygen -t ed25519 -C "haproxy-cert-deploy"

# ssh key kopieren
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@172.20.0.1
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@172.20.0.2
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@172.20.0.3



# ssl script erstellen

# ins home verzeichniss
cd /root
nano deploy_haproxy_certs.sh
```
deploy_haproxy_certs.sh
```bash
#!/bin/bash

# ==========================================
# Konfiguration
# ==========================================
DOMAIN="agt-app.de"
LE_DIR="/etc/letsencrypt/live/$DOMAIN"
# Komma-getrennte Liste der Server-IPs oder Hostnamen
SERVERS="172.20.0.1,172.20.0.2,172.20.0.3"
SSH_USER="root"
SSH_KEY="/root/.ssh/id_rsa"

TARGET_DIR="/docker/haproxy/ssl"
CONTAINER_NAME="haproxy_gateway"
# ADMIN_EMAIL="admin@ljoswig.de"

LOG_FILE="/tmp/haproxy_cert_deploy.log"
STATE_FILE="/var/log/haproxy_cert_last_md5.txt"
TMP_CERT="/tmp/haproxy.pem"

# ==========================================
# Initialisierung & Logging
# ==========================================
> "$LOG_FILE" # Log leeren
exec > >(tee -a "$LOG_FILE") 2>&1 # Alle Ausgaben ins Log und auf die Konsole umleiten

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starte Zertifikats-Deployment für $DOMAIN"

# ==========================================
# 1. Prüfen, ob ein neues Zertifikat vorliegt
# ==========================================
if [ ! -f "$LE_DIR/fullchain.pem" ] || [ ! -f "$LE_DIR/privkey.pem" ]; then
    echo "FEHLER: Let's Encrypt Zertifikate nicht gefunden in $LE_DIR"
    echo "Deployment abgebrochen." | mail -s "FEHLER: HAProxy Zertifikat Deployment" "$ADMIN_EMAIL" < "$LOG_FILE"
    exit 1
fi

CURRENT_MD5=$(md5sum "$LE_DIR/fullchain.pem" | awk '{print $1}')

if [ -f "$STATE_FILE" ]; then
    LAST_MD5=$(cat "$STATE_FILE")
    if [ "$CURRENT_MD5" == "$LAST_MD5" ]; then
        echo "[INFO] Zertifikat hat sich nicht geändert. Kein Deployment notwendig."
        exit 0
    fi
fi

echo "[INFO] Neues Zertifikat erkannt. Bereite Deployment vor..."

# ==========================================
# 2. Zertifikat für HAProxy formatieren
# ==========================================
# HAProxy erwartet Public Key (Fullchain) und Private Key in einer Datei
cat "$LE_DIR/fullchain.pem" "$LE_DIR/privkey.pem" > "$TMP_CERT"
chmod 600 "$TMP_CERT"

# ==========================================
# 3. Server sequenziell updaten
# ==========================================
IFS=',' read -ra SERVER_ARRAY <<< "$SERVERS"

for SERVER in "${SERVER_ARRAY[@]}"; do
    echo "---------------------------------------------------"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Verarbeite Server: $SERVER"
    
    # 3.1 Zertifikat kopieren
    echo "  -> Kopiere Zertifikat..."
    scp -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$TMP_CERT" "${SSH_USER}@${SERVER}:${TARGET_DIR}/haproxy.pem"
    if [ $? -ne 0 ]; then
        echo "  [FEHLER] SCP fehlgeschlagen auf $SERVER!"
        # mail -s "CRITICAL: HAProxy Cert Deployment fehlgeschlagen auf $SERVER" "$ADMIN_EMAIL" < "$LOG_FILE"
        exit 1
    fi

    # 3.2 HAProxy Config Check ausführen
    echo "  -> Prüfe HAProxy Konfiguration im Container..."
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "${SSH_USER}@${SERVER}" "docker exec $CONTAINER_NAME haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg"
    if [ $? -ne 0 ]; then
        echo "  [FEHLER] Config Check fehlgeschlagen auf $SERVER! Zertifikat ungültig oder Config fehlerhaft."
        # mail -s "CRITICAL: HAProxy Config Check fehlgeschlagen auf $SERVER" "$ADMIN_EMAIL" < "$LOG_FILE"
        exit 1
    fi

    # 3.3 Hitless Reload anstoßen (SIGUSR2 für HAProxy Master)
    echo "  -> Führe hitless Reload durch..."
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "${SSH_USER}@${SERVER}" "docker kill -s USR2 $CONTAINER_NAME"
    if [ $? -ne 0 ]; then
        echo "  [FEHLER] Reload-Befehl fehlgeschlagen auf $SERVER!"
        # mail -s "CRITICAL: HAProxy Reload fehlgeschlagen auf $SERVER" "$ADMIN_EMAIL" < "$LOG_FILE"
        exit 1
    fi

    # 3.4 Warten und Health-Status prüfen
    echo "  -> Warte 15 Sekunden auf den Docker Healthcheck..."
    sleep 15
    
    HEALTH=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "${SSH_USER}@${SERVER}" "docker inspect --format='{{json .State.Health.Status}}' $CONTAINER_NAME | tr -d '\"'")
    
    if [ "$HEALTH" != "healthy" ]; then
        echo "  [FEHLER] HAProxy Container auf $SERVER ist nicht 'healthy'! Aktueller Status: $HEALTH"
        # mail -s "CRITICAL: HAProxy Container Crash nach Reload auf $SERVER" "$ADMIN_EMAIL" < "$LOG_FILE"
        exit 1
    fi

    echo "  [OK] Server $SERVER erfolgreich aktualisiert und ist healthy."
done

# ==========================================
# 4. Aufräumen & Abschluss
# ==========================================
rm -f "$TMP_CERT"
echo "$CURRENT_MD5" > "$STATE_FILE"

echo "---------------------------------------------------"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Deployment auf allen Servern erfolgreich abgeschlossen!"

# Erfolgsmail (optional, kann auch auskommentiert werden, wenn es nervt)
# mail -s "SUCCESS: HAProxy Zertifikate erneuert" "$ADMIN_EMAIL" < "$LOG_FILE"

exit 0



```

```bash
# script ausführbar machen
# noch nicht ausführen (haproxy config fehlt noch)
chmod +x deploy_haproxy_certs.sh

```



## Installation Datenbanken

```bash

# Docker config
sudo mkdir -p /docker/redis

# Linux Ram zuweisung für Linux anpassen. 
sudo sysctl vm.overcommit_memory=1

# Dauerhaft speichern (überlebt Reboot)
echo "vm.overcommit_memory = 1" | sudo tee -a /etc/sysctl.conf


cd /docker/redis
sudo nano .env

```

env file für redis (alle server)
```bash
# .env

# --- PASSWÖRTER (Ändern!) ---
REDIS_SYNC_PASSWORD=DeinSicheresPasswort123
REDIS_RATELIMIT_PASSWORD=DeinSicheresPasswort123

# --- NETZWERK KONFIGURATION ---
# Die WireGuard IPs der Nodes
IP_AGT1=172.20.0.1
IP_AGT2=172.20.0.2
IP_AGT3=172.20.0.3

# --- PORTS (Nicht ändern, wenn nicht nötig) ---
# Instanz 1: Sync
PORT_REDIS_SYNC=6379
PORT_SENTINEL_SYNC=26379

# Instanz 2: RateLimit
PORT_REDIS_RL=6380
PORT_SENTINEL_RL=26380

# --- SENTINEL KONFIGURATION ---
# Wie viele Sentinels müssen zustimmen? (2 von 3)
SENTINEL_QUORUM=2
SENTINEL_DOWN_AFTER=5000
SENTINEL_FAILOVER_TIMEOUT=60000

```

Für alle Server mit verschieden configs:
```bash
sudo nano docker-compose.yaml
```

AGT1
```bash

services:
  # -------------------------
  # USE CASE 1: SYNC (Master)
  # -------------------------
  redis-sync:
    image: redis:7-alpine
    container_name: redis-sync
    restart: unless-stopped
    ports:
      - "${PORT_REDIS_SYNC}:${PORT_REDIS_SYNC}"
    volumes:
      - redis-sync-data:/data
    command: >
      redis-server 
      --port ${PORT_REDIS_SYNC} 
      --requirepass ${REDIS_SYNC_PASSWORD} 
      --masterauth ${REDIS_SYNC_PASSWORD} 
      --replica-announce-ip ${IP_AGT1} 
      --replica-announce-port ${PORT_REDIS_SYNC}
      --appendonly yes

  sentinel-sync:
    image: redis:7-alpine
    container_name: sentinel-sync
    restart: unless-stopped
    ports:
      - "${PORT_SENTINEL_SYNC}:${PORT_SENTINEL_SYNC}"
    command: >
      sh -c "echo 'port ${PORT_SENTINEL_SYNC}' > /tmp/sentinel.conf &&
      echo 'sentinel monitor mymaster-sync ${IP_AGT1} ${PORT_REDIS_SYNC} ${SENTINEL_QUORUM}' >> /tmp/sentinel.conf &&
      echo 'sentinel auth-pass mymaster-sync ${REDIS_SYNC_PASSWORD}' >> /tmp/sentinel.conf &&
      echo 'sentinel down-after-milliseconds mymaster-sync ${SENTINEL_DOWN_AFTER}' >> /tmp/sentinel.conf &&
      echo 'sentinel failover-timeout mymaster-sync ${SENTINEL_FAILOVER_TIMEOUT}' >> /tmp/sentinel.conf &&
      echo 'sentinel announce-ip ${IP_AGT1}' >> /tmp/sentinel.conf &&
      echo 'sentinel announce-port ${PORT_SENTINEL_SYNC}' >> /tmp/sentinel.conf &&
      echo 'requirepass ${REDIS_SYNC_PASSWORD}' >> /tmp/sentinel.conf &&
      redis-sentinel /tmp/sentinel.conf"
    depends_on:
      - redis-sync

  # ------------------------------
  # USE CASE 2: RATE LIMIT (Master)
  # ------------------------------
  redis-ratelimit:
    image: redis:7-alpine
    container_name: redis-ratelimit
    restart: unless-stopped
    ports:
      - "${PORT_REDIS_RL}:${PORT_REDIS_RL}"
    volumes:
      - redis-ratelimit-data:/data
    command: >
      redis-server 
      --port ${PORT_REDIS_RL} 
      --requirepass ${REDIS_RATELIMIT_PASSWORD} 
      --masterauth ${REDIS_RATELIMIT_PASSWORD} 
      --replica-announce-ip ${IP_AGT1} 
      --replica-announce-port ${PORT_REDIS_RL}
      --appendonly yes

  sentinel-ratelimit:
    image: redis:7-alpine
    container_name: sentinel-ratelimit
    restart: unless-stopped
    ports:
      - "${PORT_SENTINEL_RL}:${PORT_SENTINEL_RL}"
    command: >
      sh -c "echo 'port ${PORT_SENTINEL_RL}' > /tmp/sentinel.conf &&
      echo 'sentinel monitor mymaster-rl ${IP_AGT1} ${PORT_REDIS_RL} ${SENTINEL_QUORUM}' >> /tmp/sentinel.conf &&
      echo 'sentinel auth-pass mymaster-rl ${REDIS_RATELIMIT_PASSWORD}' >> /tmp/sentinel.conf &&
      echo 'sentinel down-after-milliseconds mymaster-rl ${SENTINEL_DOWN_AFTER}' >> /tmp/sentinel.conf &&
      echo 'sentinel failover-timeout mymaster-rl ${SENTINEL_FAILOVER_TIMEOUT}' >> /tmp/sentinel.conf &&
      echo 'sentinel announce-ip ${IP_AGT1}' >> /tmp/sentinel.conf &&
      echo 'sentinel announce-port ${PORT_SENTINEL_RL}' >> /tmp/sentinel.conf &&
      echo 'requirepass ${REDIS_RATELIMIT_PASSWORD}' >> /tmp/sentinel.conf &&
      redis-sentinel /tmp/sentinel.conf"
    depends_on:
      - redis-ratelimit
        
  # --- MONITORING AGENT ---
  redis-exporter:
    image: oliver006/redis_exporter:v1.67.0-alpine
    container_name: redis-exporter
    network_mode: host
    restart: unless-stopped
    command: 
      - '--web.listen-address=:9121' # Hört auf Port 9121

volumes:
  redis-sync-data:
  redis-ratelimit-data:

```

AGT2
```bash
services:
  # -------------------------
  # USE CASE 1: SYNC (Replica)
  # -------------------------
  redis-sync:
    image: redis:7-alpine
    container_name: redis-sync
    restart: unless-stopped
    ports:
      - "${PORT_REDIS_SYNC}:${PORT_REDIS_SYNC}"
    volumes:
      - redis-sync-data:/data
    command: >
      redis-server 
      --port ${PORT_REDIS_SYNC} 
      --requirepass ${REDIS_SYNC_PASSWORD} 
      --masterauth ${REDIS_SYNC_PASSWORD} 
      --replicaof ${IP_AGT1} ${PORT_REDIS_SYNC}
      --replica-announce-ip ${IP_AGT2} 
      --replica-announce-port ${PORT_REDIS_SYNC}
      --appendonly yes

  sentinel-sync:
    image: redis:7-alpine
    container_name: sentinel-sync
    restart: unless-stopped
    ports:
      - "${PORT_SENTINEL_SYNC}:${PORT_SENTINEL_SYNC}"
    command: >
      sh -c "echo 'port ${PORT_SENTINEL_SYNC}' > /tmp/sentinel.conf &&
      echo 'sentinel monitor mymaster-sync ${IP_AGT1} ${PORT_REDIS_SYNC} ${SENTINEL_QUORUM}' >> /tmp/sentinel.conf &&
      echo 'sentinel auth-pass mymaster-sync ${REDIS_SYNC_PASSWORD}' >> /tmp/sentinel.conf &&
      echo 'sentinel down-after-milliseconds mymaster-sync ${SENTINEL_DOWN_AFTER}' >> /tmp/sentinel.conf &&
      echo 'sentinel failover-timeout mymaster-sync ${SENTINEL_FAILOVER_TIMEOUT}' >> /tmp/sentinel.conf &&
      echo 'sentinel announce-ip ${IP_AGT2}' >> /tmp/sentinel.conf &&
      echo 'sentinel announce-port ${PORT_SENTINEL_SYNC}' >> /tmp/sentinel.conf &&
      echo 'requirepass ${REDIS_SYNC_PASSWORD}' >> /tmp/sentinel.conf &&
      redis-sentinel /tmp/sentinel.conf"
    depends_on:
      - redis-sync

  # ------------------------------
  # USE CASE 2: RATE LIMIT (Replica)
  # ------------------------------
  redis-ratelimit:
    image: redis:7-alpine
    container_name: redis-ratelimit
    restart: unless-stopped
    ports:
      - "${PORT_REDIS_RL}:${PORT_REDIS_RL}"
    volumes:
      - redis-ratelimit-data:/data
    command: >
      redis-server 
      --port ${PORT_REDIS_RL} 
      --requirepass ${REDIS_RATELIMIT_PASSWORD} 
      --masterauth ${REDIS_RATELIMIT_PASSWORD} 
      --replicaof ${IP_AGT1} ${PORT_REDIS_RL}
      --replica-announce-ip ${IP_AGT2} 
      --replica-announce-port ${PORT_REDIS_RL}
      --appendonly yes

  sentinel-ratelimit:
    image: redis:7-alpine
    container_name: sentinel-ratelimit
    restart: unless-stopped
    ports:
      - "${PORT_SENTINEL_RL}:${PORT_SENTINEL_RL}"
    command: >
      sh -c "echo 'port ${PORT_SENTINEL_RL}' > /tmp/sentinel.conf &&
      echo 'sentinel monitor mymaster-rl ${IP_AGT1} ${PORT_REDIS_RL} ${SENTINEL_QUORUM}' >> /tmp/sentinel.conf &&
      echo 'sentinel auth-pass mymaster-rl ${REDIS_RATELIMIT_PASSWORD}' >> /tmp/sentinel.conf &&
      echo 'sentinel down-after-milliseconds mymaster-rl ${SENTINEL_DOWN_AFTER}' >> /tmp/sentinel.conf &&
      echo 'sentinel failover-timeout mymaster-rl ${SENTINEL_FAILOVER_TIMEOUT}' >> /tmp/sentinel.conf &&
      echo 'sentinel announce-ip ${IP_AGT2}' >> /tmp/sentinel.conf &&
      echo 'sentinel announce-port ${PORT_SENTINEL_RL}' >> /tmp/sentinel.conf &&
      echo 'requirepass ${REDIS_RATELIMIT_PASSWORD}' >> /tmp/sentinel.conf &&
      redis-sentinel /tmp/sentinel.conf"
    depends_on:
      - redis-ratelimit
        
  # --- MONITORING AGENT ---
  redis-exporter:
    image: oliver006/redis_exporter:v1.67.0-alpine
    container_name: redis-exporter
    network_mode: host
    restart: unless-stopped
    command: 
      - '--web.listen-address=:9121'

volumes:
  redis-sync-data:
  redis-ratelimit-data:
```

AGT3
```bash
services:
  # -------------------------
  # USE CASE 1: SYNC (Replica)
  # -------------------------
  redis-sync:
    image: redis:7-alpine
    container_name: redis-sync
    restart: unless-stopped
    ports:
      - "${PORT_REDIS_SYNC}:${PORT_REDIS_SYNC}"
    volumes:
      - redis-sync-data:/data
    command: >
      redis-server 
      --port ${PORT_REDIS_SYNC} 
      --requirepass ${REDIS_SYNC_PASSWORD} 
      --masterauth ${REDIS_SYNC_PASSWORD} 
      --replicaof ${IP_AGT1} ${PORT_REDIS_SYNC}
      --replica-announce-ip ${IP_AGT3} 
      --replica-announce-port ${PORT_REDIS_SYNC}
      --appendonly yes

  sentinel-sync:
    image: redis:7-alpine
    container_name: sentinel-sync
    restart: unless-stopped
    ports:
      - "${PORT_SENTINEL_SYNC}:${PORT_SENTINEL_SYNC}"
    command: >
      sh -c "echo 'port ${PORT_SENTINEL_SYNC}' > /tmp/sentinel.conf &&
      echo 'sentinel monitor mymaster-sync ${IP_AGT1} ${PORT_REDIS_SYNC} ${SENTINEL_QUORUM}' >> /tmp/sentinel.conf &&
      echo 'sentinel auth-pass mymaster-sync ${REDIS_SYNC_PASSWORD}' >> /tmp/sentinel.conf &&
      echo 'sentinel down-after-milliseconds mymaster-sync ${SENTINEL_DOWN_AFTER}' >> /tmp/sentinel.conf &&
      echo 'sentinel failover-timeout mymaster-sync ${SENTINEL_FAILOVER_TIMEOUT}' >> /tmp/sentinel.conf &&
      echo 'sentinel announce-ip ${IP_AGT3}' >> /tmp/sentinel.conf &&
      echo 'sentinel announce-port ${PORT_SENTINEL_SYNC}' >> /tmp/sentinel.conf &&
      echo 'requirepass ${REDIS_SYNC_PASSWORD}' >> /tmp/sentinel.conf &&
      redis-sentinel /tmp/sentinel.conf"
    depends_on:
      - redis-sync

  # ------------------------------
  # USE CASE 2: RATE LIMIT (Replica)
  # ------------------------------
  redis-ratelimit:
    image: redis:7-alpine
    container_name: redis-ratelimit
    restart: unless-stopped
    ports:
      - "${PORT_REDIS_RL}:${PORT_REDIS_RL}"
    volumes:
      - redis-ratelimit-data:/data
    command: >
      redis-server 
      --port ${PORT_REDIS_RL} 
      --requirepass ${REDIS_RATELIMIT_PASSWORD} 
      --masterauth ${REDIS_RATELIMIT_PASSWORD} 
      --replicaof ${IP_AGT1} ${PORT_REDIS_RL}
      --replica-announce-ip ${IP_AGT3} 
      --replica-announce-port ${PORT_REDIS_RL}
      --appendonly yes

  sentinel-ratelimit:
    image: redis:7-alpine
    container_name: sentinel-ratelimit
    restart: unless-stopped
    ports:
      - "${PORT_SENTINEL_RL}:${PORT_SENTINEL_RL}"
    command: >
      sh -c "echo 'port ${PORT_SENTINEL_RL}' > /tmp/sentinel.conf &&
      echo 'sentinel monitor mymaster-rl ${IP_AGT1} ${PORT_REDIS_RL} ${SENTINEL_QUORUM}' >> /tmp/sentinel.conf &&
      echo 'sentinel auth-pass mymaster-rl ${REDIS_RATELIMIT_PASSWORD}' >> /tmp/sentinel.conf &&
      echo 'sentinel down-after-milliseconds mymaster-rl ${SENTINEL_DOWN_AFTER}' >> /tmp/sentinel.conf &&
      echo 'sentinel failover-timeout mymaster-rl ${SENTINEL_FAILOVER_TIMEOUT}' >> /tmp/sentinel.conf &&
      echo 'sentinel announce-ip ${IP_AGT3}' >> /tmp/sentinel.conf &&
      echo 'sentinel announce-port ${PORT_SENTINEL_RL}' >> /tmp/sentinel.conf &&
      echo 'requirepass ${REDIS_RATELIMIT_PASSWORD}' >> /tmp/sentinel.conf &&
      redis-sentinel /tmp/sentinel.conf"
    depends_on:
      - redis-ratelimit
        
  # --- MONITORING AGENT ---
  redis-exporter:
    image: oliver006/redis_exporter:v1.67.0-alpine
    container_name: redis-exporter
    network_mode: host
    restart: unless-stopped
    command: 
      - '--web.listen-address=:9121'

volumes:
  redis-sync-data:
  redis-ratelimit-data:

```


```bash
# jetzt starten. zuerst node 1, dann node 2 und zum schluss node 3 mit ca 5 sekunde verzögerung
sudo docker compose up -d

## weiter mit yugabyte
sudo mkdir /docker/yugabyte
cd /docker/yugabyte

sudo nano .env
sudo nano docker-compose.yaml
```

env für agt1
```bash
NODE_NAME=postgres-node1
# Ersetze dies durch die echte (LAN/WAN) IP-Adresse von Server 1
NODE_IP=172.20.0.1
# Bleibt leer, da dies der erste Knoten des Clusters ist
JOIN_FLAG=""      
YSQL_PASSWORD=hVU8ng4qXvDZ

```
env für agt2
```bash
NODE_NAME=postgres-node2
# Ersetze dies durch die echte IP-Adresse von Server 2
NODE_IP=172.20.0.2
# Verbindet sich mit der IP von Server 1
JOIN_FLAG="--join=172.20.0.1" 
YSQL_PASSWORD=hVU8ng4qXvDZ
```
env für agt3
```bash
NODE_NAME=postgres-node3
# Ersetze dies durch die echte IP-Adresse von Server 3
NODE_IP=172.20.0.3
# Verbindet sich ebenfalls mit der IP von Server 1
JOIN_FLAG="--join=172.20.0.1" 
YSQL_PASSWORD=hVU8ng4qXvDZ
```

Für alle nodes

```yaml
services:
  yugabyte-node:
    image: yugabytedb/yugabyte:latest
    container_name: ${NODE_NAME}
    network_mode: "host"
    restart: unless-stopped
    
    # --- NEU: Ulimits für YugabyteDB ---
    ulimits:
      nofile:
        soft: 1048576
        hard: 1048576
    # -----------------------------------

    command: >
      sh -c 'bin/yugabyted start 
      --base_dir=/home/yugabyte/yb_data 
      --daemon=false 
      --listen=${NODE_IP} 
      ${JOIN_FLAG}'
    volumes:
      - yb_data:/home/yugabyte/yb_data
    environment:
      - YSQL_PASSWORD=${YSQL_PASSWORD}

volumes:
  yb_data:
```


```bash
# jetzt starten. zuerst node 1, dann node 2 und zum schluss node 3 mit ca 20 sekunde verzögerung
sudo docker compose up -d
```


## Installation der apis / web-frontends


Die Docker configs:

auf allen servern:
```
mkdir /docker/agt-v3
cd /docker/agt-v3
nano docker-compose.yaml
```

```yaml
services:
  # --- WEB FRONTENDS (Apache) ---
  client-apache:
    image: ghcr.io/lennart441/agt-app/client-apache:v3
    container_name: client-apache
    ports:
      - "3101:80"
    restart: unless-stopped
    env_file: .env

  dashboard-apache:
    image: ghcr.io/lennart441/agt-app/dashboard-apache:v3
    container_name: dashboard-apache
    ports:
      - "3102:80"
    restart: unless-stopped
    env_file: .env
    environment:
      - API_BASE=${API_BASE}

  # --- BACKEND SERVICES (Node.js) ---
  sync-server:
    image: ghcr.io/lennart441/agt-app/sync-server:v3
    container_name: sync-server
    env_file: .env
    ports:
      - "3111:3000"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3000/v3/sync-api/health"]
      interval: 30s
      timeout: 5s
      retries: 3

  report-server:
    image: ghcr.io/lennart441/agt-app/report-server:v3
    container_name: report-server
    env_file: .env
    ports:
      - "3112:3002"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3002/v3/report/health"]
      interval: 10s
      timeout: 5s
      retries: 3

  primaer-api:
    image: ghcr.io/lennart441/agt-app/primaer-api:v3
    container_name: primaer-api
    env_file: .env
    ports:
      - "3113:3001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3001/v3/pri-api/health"]
      interval: 10s
      timeout: 5s
      retries: 3

  agt-get-api:
    image: ghcr.io/lennart441/agt-app/agt-get-api:v3
    container_name: agt-get-api
    env_file: .env
    ports:
      - "3114:3011"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3011/v3/agt-get-api/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

```bash
nano .env
```


```env
################################
########### Databases ##########
################################

  
# --- Datenbank-Konfiguration YugabyteDB Cluster(flexibel für variable Host-Anzahl mit individuellen Ports) ---
DB_USER=yugabyte
DB_PASSWORD=hVU8ng4qXvDZ
DB_DATABASE=agt-data
# Kommagetrennte Liste von Hosts mit Ports (Format: host:port,host:port)
DB_HOSTS=172.20.0.1:5433,172.20.0.2:5433,172.20.0.3:5433

  
# Redis Sentinel Konfiguration für Sync-Server
REDIS_NODES_SYNC=172.20.0.1:26379,172.20.0.2:26379,172.20.0.3:26379
REDIS_MASTER_NAME_SYNC=mymaster-sync


# Redis Sentinel Konfiguration für Rate-Limiting
REDIS_NODES_RATELIMIT=172.20.0.1:26380,172.20.0.2:26380,172.20.0.3:26380
REDIS_MASTER_NAME_RATELIMIT=mymaster-rl


# Gemeinsames Passwort für beide Redis-Cluster (optional, falls gesetzt)
REDIS_PASSWORD=DeinSicheresPasswort123


################################
########### API-Config #########
################################

  

# Mail service configuration
MAIL_HOST=mail.ljoswig.de
MAIL_PORT=465
MAIL_USER=no-reply@ljoswig.de
MAIL_FROM=no-reply@ljoswig.de
MAIL_PASSWORD=20AtellyTenDpoWEReTraBsTIaLLOatemb

  

# Primär API configuration
JWT_SECRET=duM0yIzYtkVYroO+xuWaX8+DyC43Guwhj+ziwk7smGOFHMUmyB8IfsteVzK4rzIskuSmqSgAVxvnpfZdtRvJ1v/cmEZ20DOpSslPfS0YDN/VZOxK1yf1WAh6T12mVC7adjXhj4RTeKgEjUc2wGo7+aeoxo1hbNMkQdBYC2nh96U=
JWT_SECRET_SYNC=duM0yIzYtkVYroO+xuWaX8+DyC43Guwhj+ziwk7smGOFHMUmyB8IfsteVzK4rzIskuSmqSgAVxvnpfZdtRvJ1v/cmEZ20DOpSslPfS0YDN/VZOxK1yf1WAh6T12mVC7adjXhj4RTeKgEjUc2wGo7+aeoxo1hbNMkQdBYC2nh96U=

# CORS erlaubte Origins (kommagetrennte Liste, z.B. http://localhost:5500,https://agt.ff-stocksee.de)
# CORS_ALLOWED_ORIGINS=http://localhost:3101,http://127.0.0.1:5500,https://agt.ff-stocksee.de,http://localhost:8080,http://localhost:3107
CORS_ALLOWED_ORIGINS=https://client.agt-app.de, https://agt-app.de
  
  

################################
######### Client config ########
################################

# --- NEU: Einheitliche Backend-Konfiguration ---  
# Liste aller Backend-Domains (kommasepariert)  
BACKEND_DOMAINS=https://agt-1.agt-app.de,https://agt-2.agt-app.de,https://agt-3.agt-app.de  
  
# API-Pfade (relativ zu den Domains)  
SYNC_API_PATH=/v3/sync-api/trupps  
AGT_GET_API_PATH=/v3/agt-get-api/data  
REPORT_API_PATH=/v3/report  
PRIMAER_API_PATH=/v3/pri-api

  

# Dashboard-Frontend API-Base  
API_BASE=https://agt-1.agt-app.de/v3/pri-api

# API Endpoint configuration for clients
SYNC_API_URL=https://agt-1.agt-app.de/v3/sync-api/trupps
REPORT_API_URL=https://agt-1.agt-app.de/v3/report-api
AGT_GET_API_URL=https://agt-1.agt-app.de/v3/agt-get-api/data

# Service Worker API-Basen und Backup-Domain
SW_PRIMARY_API_BASE=http://agt-1.agt-app.de/v3/sync-api/
SW_AGT_GET_API_BASE=http://agt-1.agt-app.de/v3/agt-get-api/
SW_REPORT_API_BASE=http://agt-1.agt-app.de/v3/report-api
SW_BACKUP_DOMAIN=https://agt-2.agt-app.de/

# Service Worker Basis-Pfad und Healthcheck-URL
SW_BASE_PATH=/
SW_HEALTHCHECK_URL=https://agt-1.agt-app.de/v3/sync-api/isReady

  

# is ready endpoint for failover checks
# Report-Server Healthcheck/Ready-Check URL
REPORT_SERVER_URL=http://report-server:3002/v3/report/ready

  

################################
########## DEVELOPMENT #########
################################
  
DEVELOPMENT_MODE=false
RATE_LIMIT_DISABLED=false
# aktuell nicht genutzt
LOG_LEVEL=debug
DEBUG_MODE=true


```


```bash
# github zugang
echo  | docker login ghcr.io -u lennart441 --password-stdin
docker compose pull



```

## Installation von haproxy

auf allen servern
```
mkdir /docker/haproxy
cd /docker/haproxy

```

die datein müssen wie folgt angelegt werden:

```
/pfad/zu/ihrem/projekt/
├── conf/  
│   ├── errors/  
│   │   ├── 404.http  
│   │   └── 503.http  
│   ├── ssl/  
│   ├── coraza.cfg  
│   ├── coraza-spoa.yaml
|   ├── promtail-config.yaml  
│   └── haproxy.cfg  
├── coraza/  
│   └── rules/  
│       ├── docs/  
│       ├── plugins/  
│       ├── regex-assembly/  
│       ├── rules/  
│       ├── tests/  
│       ├── util/  
│       ├── CHANGES.md  
│       ├── CONTRIBUTING.md  
│       ├── CONTRIBUTORS.md  
│       ├── crs-setup.conf.example  
│       ├── INSTALL.md  
│       ├── KNOWN_BUGS.md  
│       ├── LICENSE  
│       ├── README.md  
│       ├── renovate.json  
│       ├── SECURITY.md  
│       └── SPONSORS.md  
├── ssl/  
│   └── haproxy.pem  
├── docker-compose.yaml  
└── Dockerfile.coraza
```


docker-compose.yaml

```yaml
services:  
 haproxy:  
   image: haproxy:3.2-alpine  
   container_name: haproxy_gateway  
  
   # Exponiert Ports 80 (HTTP) und 443 (HTTPS) auf dem Host-System  
   ports:  
     - "80:80"  
     - "443:443"  
       # Dashbored HAPROXY
     - "56708:56708"  
       # HA-Proxy Cluster Ports    
     - "50000:50000"
       # Prometheus Dashbored                              
      - "8404:8404"
  
  
   # Volumes für Konfiguration und SSL-Zertifikate  
   volumes:  
     # 1. HAProxy Konfigurationsdatei (READ-ONLY)  
     - ./conf/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro  
     # 2. SSL-Zertifikate (READ-ONLY)  
     - ./ssl:/etc/ssl/certs:ro  
     # NEU: Fehlerdateien (READ-ONLY)  
     - ./conf/errors:/usr/local/etc/haproxy/errors:ro  
     # Corsa Datein    
     - ./conf/coraza.cfg:/usr/local/etc/haproxy/coraza.cfg:ro  
     - ./conf/coraza-spoa.yaml:/etc/coraza-spoa/config.yaml:ro  
  
   # HINWEIS ZU sysctls:  
   # Die neueren Versionen (ab 2.4+) laufen standardmäßig als USER 'haproxy' (non-root).  
   sysctls:  
     net.ipv4.ip_unprivileged_port_start: 0  
  
   # Best Practice: Healthcheck  
   healthcheck:  
     test: ["CMD", "haproxy", "-c", "-f", "/usr/local/etc/haproxy/haproxy.cfg"]  
     interval: 10s  
     timeout: 5s  
     retries: 3  
     start_period: 10s  
  
   # Best Practice: Neustart-Strategie  
   restart: always  
  
   depends_on:  
     coraza-spoa:  
       condition: service_started  
  
   # Best Practice: Ressourcen-Limits    
   deploy:  
     resources:  
       limits:  
         cpus: '0.5'  
         memory: 128M  
  
   networks:  
        - security-net  
  
# NEU: Coraza WAF als SPOE-Agent  
 coraza-spoa:  
   build:  
     context: .  
     dockerfile: Dockerfile.coraza  
   container_name: coraza-spoa  
   command: ["-config", "/etc/coraza-spoa/config.yaml"]  
   volumes:  
     - ./conf/coraza-spoa.yaml:/etc/coraza-spoa/config.yaml:ro  
     # Hier laden wir die OWASP Regeln (Stateless)  
     - ./coraza/rules:/etc/coraza-spoa/rules:ro    
   restart: always  
   deploy:  
     resources:  
       limits:  
         memory: 384M  
   networks:  
     security-net:  
       aliases:  
         - coraza-spoa  
           
           
  promtail:
    image: grafana/promtail:latest
    container_name: promtail
    volumes:
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./conf/promtail-config.yaml:/etc/promtail/config.yml:ro
    command: -config.file=/etc/promtail/config.yml
    restart: always


  
networks:  
 security-net:  
root@agt-1:/docker/haproxy#
```

404.http
```http
HTTP/1.0 404 Not Found
Cache-Control: no-cache
Connection: close
Content-Type: text/plain
Content-Length: 14

404 Not Found

```

503.http
```http
HTTP/1.0 503 Service Unavailable
Cache-Control: no-cache
Connection: close
Content-Type: text/plain
Content-Length: 24

503 Service Unavailable

```

coraza.cfg 
```cfg
# /docker/haproxy/conf/coraza.cfg  
[coraza]  
spoe-agent coraza-agent  
  # HIER GEÄNDERT: coraza-req statt check-request  
  messages coraza-req  
  option var-prefix coraza  
  timeout hello      500ms  
  timeout idle       60s  
  timeout processing 1s  
  use-backend coraza-spoa  
  
# HIER GEÄNDERT: coraza-req statt check-request  
spoe-message coraza-req  
  event on-frontend-http-request  
  args app=str(agt_waf) id=unique-id src-ip=src method=method path=path query=query version=req.ver headers=req.hdrs body=req.body

````
coraza-spoa.yaml
```
# /docker/haproxy/conf/coraza-spoa.yaml  
bind: 0.0.0.0:9000  
default_application: agt_waf  
log_level: info  
log_format: console  
  
applications:  
 - name: agt_waf  
   log_level: info  
   log_format: console  
   directives: |  
     Include @coraza.conf-recommended  
  
     # 1. WAF WIRD SCHARFGESCHALTET!     
     SecRuleEngine On  
  
     SecRequestBodyAccess On  
     # Großzügiges Limit für deine Berichte (1 MB)  
     SecRequestBodyLimit 1048576    
     SecRequestBodyNoFilesLimit 1048576  
  
     # 2. NEU: JSON-Parsing zwingend aktivieren für deine APIs!  
     SecRule REQUEST_HEADERS:Content-Type "@rx (?i)application/json" \  
       "id:999001,phase:1,nolog,pass,ctl:requestBodyProcessor=JSON"  
  
     Include /etc/coraza-spoa/rules/crs-setup.conf.example  
     Include /etc/coraza-spoa/rules/rules/*.conf  
  
     # Deine manuelle Testregel (kannst du drin lassen oder entfernen)  
     SecRule ARGS:testwaf "123" "id:190001,phase:2,deny,status:403,msg:'Coraza WAF Test Block'"  
  
     # 3. NOTFALL-WHITELISTING VORLAGE (Auskommentiert)  
     # Falls eine spezifische Route (z.B. der Report-Upload) doch False Positives wirft,    
     # kannst du die WAF nur für DIESE Route wieder in den Beobachtungsmodus versetzen:  
     # SecRule REQUEST_URI "@beginsWith /v3/report/" "id:999002,phase:1,pass,nolog,ctl:ruleEngine=DetectionOnly"

```

Dockerfile.coraza

```
# Stage 1: Build  
# Wir nutzen eine aktuelle Go-Version, um die Anforderungen des Quellcodes zu erfüllen  
FROM golang:1.26-alpine AS builder  
  
# Installiere git für das Klonen des Repositories  
RUN apk add --no-cache git  
  
# Installiere mage direkt über Go  
RUN go install github.com/magefile/mage@latest  
  
WORKDIR /app  
# Wir holen uns den aktuellsten Stand von Coraza-SPOA  
RUN git clone https://github.com/corazawaf/coraza-spoa.git .  
  
# Baue die Anwendung (mage nutzt nun die installierte Go 1.26+ Umgebung)  
RUN mage build  
  
# Stage 2: Run (Schlankes End-Image für deine 80GB SSD)  
FROM alpine:latest  
RUN apk add --no-cache ca-certificates  
WORKDIR /app  
  
# Wir kopieren nur die fertige Binary, das spart Platz und RAM  
COPY --from=builder /app/build/coraza-spoa /usr/local/bin/coraza-spoa  
  
# Startbefehl  
ENTRYPOINT ["coraza-spoa"]

```

promtail-config.yaml

```yaml
server:
  http_listen_port: 9080
  grpc_listen_port: 0

clients:
  - url: http://172.20.0.99:3100/loki/api/v1/push

scrape_configs:
  - job_name: docker
    docker_sd_configs:
      - host: unix:///var/run/docker.sock
        refresh_interval: 5s
    relabel_configs:
      # 1. Container-Namen sauber extrahieren (ohne den Slash am Anfang)
      - source_labels: ['__meta_docker_container_name']
        regex: '/(.*)'
        target_label: 'container'
      
      # 2. Nur den HAProxy-Container behalten
      - source_labels: ['container']
        regex: 'haproxy_gateway'
        action: keep
      
      # 3. FIX: Wir vergeben ein festes 'job'-Label für Loki
      - target_label: 'job'
        replacement: 'haproxy'

```


für die datein im rules ordner
```bash
cd /docker/haproxy/coraza/rules/
git clone https://github.com/coreruleset/coreruleset.git .
````

die haproxy cfg datein
AGT-1
```c
global   
   nbthread 1  
   log stdout format raw local0 info
   user haproxy  
   group haproxy  
   maxconn 25000  
   tune.ssl.default-dh-param 2048  

resolvers docker
   nameserver dns1 127.0.0.11:53
   resolve_retries 3
   timeout resolve 1s
   timeout retry   1s
   hold valid      10s
  
defaults  
   mode http  
   log global
   # log-format '{"client_ip":"%ci","timestamp":"%t","method":"%HM","path":"%HP","proto":"%HV","status":%ST,"bytes_read":%B,"duration_ms":%Tr,"termination_state":"%ts","trace_id":"%[capture.req.hdr(0)]","user_agent":"%[capture.req.hdr(1)]"}'  
   timeout connect 5s  
   timeout client  30s  
   timeout server  30s  
   timeout http-request 15s  
   errorfile 503 /usr/local/etc/haproxy/errors/503.http  

# ----------------------------------------------------------------------  
# Frontends (Logik bleibt gleich)  
# ----------------------------------------------------------------------  
frontend http_in
   bind :80
   # Korrekte Syntax für Version 3.2
   filter spoe engine coraza config /usr/local/etc/haproxy/coraza.cfg
   capture request header X-Trace-ID len 64
   capture request header User-Agent len 256
   http-request redirect scheme https code 301

frontend stats
   mode http
   bind :56708
   stats enable
   stats refresh 10s
   stats uri /kM3liYHB
   stats show-modules
   stats admin if TRUE
   stats auth admin:RaLL8ATBg274qpTs  
   
frontend prometheus
   bind :8404
   mode http
   http-request use-service prometheus-exporter if { path /metrics }

frontend https_in
   bind :443 ssl crt /etc/ssl/certs/haproxy.pem
   
   capture request header X-Trace-ID len 64
   capture request header User-Agent len 256

   # 1. WAF Filter aktivieren
   filter spoe engine coraza config /usr/local/etc/haproxy/coraza.cfg


   # 2. HAProxy anweisen, die Anfrage zu blockieren, wenn Coraza das sagt
   # (Die Variable txn.coraza.action wird vom SPOE-Agenten gesetzt)
   http-request deny deny_status 403 if { var(txn.coraza.action) -m str deny }

   # Das Log-Format greift auf diese Sslots über den Index zu:
   # %[capture.req.hdr(0)] entspricht X-Trace-ID
   # %[capture.req.hdr(1)] entspricht User-Agent
   log-format '{"client_ip":"%ci","timestamp":"%t","method":"%HM","path":"%HP","proto":"%HV","status":%ST,"bytes_read":%B,"duration_ms":%Tr,"termination_state":"%ts","trace_id":"%[capture.req.hdr(0)]","user_agent":"%[capture.req.hdr(1)]"}'


   http-request set-header X-Real-IP %[src]  
  
   # ACLs  
   acl host_api       hdr(host) -i agt-1.agt-app.de  
   acl host_client    hdr(host) -i client.agt-app.de  
   acl host_website   hdr(host) -i agt-app.de  
  
   acl path_sync_api     path_beg /v3/sync-api  
   acl path_report_api   path_beg /v3/report  
   acl path_primaer_api  path_beg /v3/pri-api  
   acl path_get_api      path_beg /v3/agt-get-api  
   acl path_dashboard    path_beg /dashboard  
  
   # Routing  
   use_backend dashboard_backend_apache if host_website path_dashboard
   use_backend website_backend          if host_website  
   use_backend api_backend_sync         if host_api path_sync_api  
   use_backend api_backend_report       if host_api path_report_api  
   use_backend api_backend_primaer      if host_api path_primaer_api  
   use_backend api_backend_get          if host_api path_get_api   
   use_backend client_backend_apache    if host_client  
  
   default_backend backend_404_error  

# ----------------------------------------------------------------------  
# Backends mit Failover (Active-Passive)  
# ----------------------------------------------------------------------  

# Website agt-app.de  
backend website_backend  
   balance roundrobin
   http-request set-path /website%[path]  
   option httpchk GET /index.html  
   server agt-1 172.20.0.1:3102 check    
   server agt-2 172.20.0.2:3102 check   
   server agt-3 172.20.0.3:3102 check   

backend api_backend_sync  
   option forwardfor
   option httpchk GET /v3/sync-api/health
   balance leastconn
   server agt-1 172.20.0.1:3111 check  
   server agt-2 172.20.0.2:3111 check 
   server agt-3 172.20.0.3:3111 check

backend api_backend_report  
   option forwardfor
   option httpchk GET /v3/report/health  
   balance leastconn
   server agt-1 172.20.0.1:3112 check  
   server agt-2 172.20.0.2:3112 check   
   server agt-3 172.20.0.3:3112 check   

backend api_backend_primaer  
   option forwardfor
   option httpchk GET /v3/pri-api/health
   balance leastconn  
   server agt-1 172.20.0.1:3113 check 
   server agt-2 172.20.0.2:3113 check  
   server agt-3 172.20.0.3:3113 check  

backend api_backend_get  
   option forwardfor
   option httpchk GET /v3/agt-get-api/health  
   balance leastconn
   server agt-1 172.20.0.1:3114 check  
   server agt-2 172.20.0.2:3114 check   
   server agt-3 172.20.0.3:3114 check   

backend client_backend_apache  
   option forwardfor
   option httpchk GET /index.html
   balance roundrobin  
   server agt-1 172.20.0.1:3101 check  
   server agt-2 172.20.0.2:3101 check   
   server agt-3 172.20.0.3:3101 check   

backend dashboard_backend_apache  
   #http-request set-path %[path,regsub(^/dashboard/,/)] if { path_beg /dashboard/ }  
   option forwardfor
   option httpchk GET /index.html 
   balance roundrobin 
   server agt-1 172.20.0.1:3102 check  
   server agt-2 172.20.0.2:3102 check   
   server agt-3 172.20.0.3:3102 check   

backend coraza-spoa
    mode tcp
    # Docker DNS Name nutzen
    server s1 coraza-spoa:9000 check 

backend backend_404_error  
   errorfile 404 /usr/local/etc/haproxy/errors/404.http
   

   
```



AGT2
```c
global   
   nbthread 1  
   log stdout format raw local0 info
   user haproxy  
   group haproxy  
   maxconn 25000  
   tune.ssl.default-dh-param 2048  

resolvers docker
   nameserver dns1 127.0.0.11:53
   resolve_retries 3
   timeout resolve 1s
   timeout retry   1s
   hold valid      10s
  
defaults  
   mode http  
   log global
   # log-format '{"client_ip":"%ci","timestamp":"%t","method":"%HM","path":"%HP","proto":"%HV","status":%ST,"bytes_read":%B,"duration_ms":%Tr,"termination_state":"%ts","trace_id":"%[capture.req.hdr(0)]","user_agent":"%[capture.req.hdr(1)]"}'  
   timeout connect 5s  
   timeout client  30s  
   timeout server  30s  
   timeout http-request 15s  
   errorfile 503 /usr/local/etc/haproxy/errors/503.http  

# ----------------------------------------------------------------------  
# Frontends (Logik bleibt gleich)  
# ----------------------------------------------------------------------  
frontend http_in
   bind :80
   # Korrekte Syntax für Version 3.2
   filter spoe engine coraza config /usr/local/etc/haproxy/coraza.cfg
   capture request header X-Trace-ID len 64
   capture request header User-Agent len 256
   http-request redirect scheme https code 301

frontend stats
   mode http
   bind :56708
   stats enable
   stats refresh 10s
   stats uri /kM3liYHB
   stats show-modules
   stats admin if TRUE
   stats auth admin:RaLL8ATBg274qpTs  

frontend prometheus
   bind :8404
   mode http
   http-request use-service prometheus-exporter if { path /metrics }
   
   
frontend https_in
   bind :443 ssl crt /etc/ssl/certs/haproxy.pem
   
   capture request header X-Trace-ID len 64
   capture request header User-Agent len 256

   # 1. WAF Filter aktivieren
   filter spoe engine coraza config /usr/local/etc/haproxy/coraza.cfg


   # 2. HAProxy anweisen, die Anfrage zu blockieren, wenn Coraza das sagt
   # (Die Variable txn.coraza.action wird vom SPOE-Agenten gesetzt)
   http-request deny deny_status 403 if { var(txn.coraza.action) -m str deny }

   # Das Log-Format greift auf diese Sslots über den Index zu:
   # %[capture.req.hdr(0)] entspricht X-Trace-ID
   # %[capture.req.hdr(1)] entspricht User-Agent
   log-format '{"client_ip":"%ci","timestamp":"%t","method":"%HM","path":"%HP","proto":"%HV","status":%ST,"bytes_read":%B,"duration_ms":%Tr,"termination_state":"%ts","trace_id":"%[capture.req.hdr(0)]","user_agent":"%[capture.req.hdr(1)]"}'


   http-request set-header X-Real-IP %[src]  
  
   # ACLs  
   acl host_api       hdr(host) -i agt-2.agt-app.de  
   acl host_client    hdr(host) -i client.agt-app.de  
   acl host_website   hdr(host) -i agt-app.de  
  
   acl path_sync_api     path_beg /v3/sync-api  
   acl path_report_api   path_beg /v3/report  
   acl path_primaer_api  path_beg /v3/pri-api  
   acl path_get_api      path_beg /v3/agt-get-api  
   acl path_dashboard    path_beg /dashboard  
  
   # Routing  
   use_backend dashboard_backend_apache if host_website path_dashboard
   use_backend website_backend          if host_website  
   use_backend api_backend_sync         if host_api path_sync_api  
   use_backend api_backend_report       if host_api path_report_api  
   use_backend api_backend_primaer      if host_api path_primaer_api  
   use_backend api_backend_get          if host_api path_get_api   
   use_backend client_backend_apache    if host_client  
  
   default_backend backend_404_error  

# ----------------------------------------------------------------------  
# Backends mit Failover (Active-Passive)  
# ----------------------------------------------------------------------  

# Website agt-app.de  
backend website_backend  
   balance roundrobin
   http-request set-path /website%[path]  
   option httpchk GET /index.html  
   server agt-1 172.20.0.1:3102 check    
   server agt-2 172.20.0.2:3102 check   
   server agt-3 172.20.0.3:3102 check   

backend api_backend_sync  
   option forwardfor
   option httpchk GET /v3/sync-api/health
   balance leastconn
   server agt-1 172.20.0.1:3111 check  
   server agt-2 172.20.0.2:3111 check 
   server agt-3 172.20.0.3:3111 check

backend api_backend_report  
   option forwardfor
   option httpchk GET /v3/report/health  
   balance leastconn
   server agt-1 172.20.0.1:3112 check  
   server agt-2 172.20.0.2:3112 check   
   server agt-3 172.20.0.3:3112 check   

backend api_backend_primaer  
   option forwardfor
   option httpchk GET /v3/pri-api/health
   balance leastconn  
   server agt-1 172.20.0.1:3113 check 
   server agt-2 172.20.0.2:3113 check  
   server agt-3 172.20.0.3:3113 check  

backend api_backend_get  
   option forwardfor
   option httpchk GET /v3/agt-get-api/health  
   balance leastconn
   server agt-1 172.20.0.1:3114 check  
   server agt-2 172.20.0.2:3114 check   
   server agt-3 172.20.0.3:3114 check   

backend client_backend_apache  
   option forwardfor
   option httpchk GET /index.html
   balance roundrobin  
   server agt-1 172.20.0.1:3101 check  
   server agt-2 172.20.0.2:3101 check   
   server agt-3 172.20.0.3:3101 check   

backend dashboard_backend_apache  
   #http-request set-path %[path,regsub(^/dashboard/,/)] if { path_beg /dashboard/ }  
   option forwardfor
   option httpchk GET /index.html 
   balance roundrobin 
   server agt-1 172.20.0.1:3102 check  
   server agt-2 172.20.0.2:3102 check   
   server agt-3 172.20.0.3:3102 check   

backend coraza-spoa
    mode tcp
    # Docker DNS Name nutzen
    server s1 coraza-spoa:9000 check 

backend backend_404_error  
   errorfile 404 /usr/local/etc/haproxy/errors/404.http
   

```



AGT3
```c
global   
   nbthread 1  
   log stdout format raw local0 info
   user haproxy  
   group haproxy  
   maxconn 25000  
   tune.ssl.default-dh-param 2048  

resolvers docker
   nameserver dns1 127.0.0.11:53
   resolve_retries 3
   timeout resolve 1s
   timeout retry   1s
   hold valid      10s
  
defaults  
   mode http  
   log global
   # log-format '{"client_ip":"%ci","timestamp":"%t","method":"%HM","path":"%HP","proto":"%HV","status":%ST,"bytes_read":%B,"duration_ms":%Tr,"termination_state":"%ts","trace_id":"%[capture.req.hdr(0)]","user_agent":"%[capture.req.hdr(1)]"}'  
   timeout connect 5s  
   timeout client  30s  
   timeout server  30s  
   timeout http-request 15s  
   errorfile 503 /usr/local/etc/haproxy/errors/503.http  

# ----------------------------------------------------------------------  
# Frontends (Logik bleibt gleich)  
# ----------------------------------------------------------------------  
frontend http_in
   bind :80
   # Korrekte Syntax für Version 3.2
   filter spoe engine coraza config /usr/local/etc/haproxy/coraza.cfg
   capture request header X-Trace-ID len 64
   capture request header User-Agent len 256
   http-request redirect scheme https code 301

frontend stats
   mode http
   bind :56708
   stats enable
   stats refresh 10s
   stats uri /kM3liYHB
   stats show-modules
   stats admin if TRUE
   stats auth admin:RaLL8ATBg274qpTs  
   
frontend prometheus
   bind :8404
   mode http
   http-request use-service prometheus-exporter if { path /metrics }

frontend https_in
   bind :443 ssl crt /etc/ssl/certs/haproxy.pem
   
   capture request header X-Trace-ID len 64
   capture request header User-Agent len 256

   # 1. WAF Filter aktivieren
   filter spoe engine coraza config /usr/local/etc/haproxy/coraza.cfg


   # 2. HAProxy anweisen, die Anfrage zu blockieren, wenn Coraza das sagt
   # (Die Variable txn.coraza.action wird vom SPOE-Agenten gesetzt)
   http-request deny deny_status 403 if { var(txn.coraza.action) -m str deny }

   # Das Log-Format greift auf diese Sslots über den Index zu:
   # %[capture.req.hdr(0)] entspricht X-Trace-ID
   # %[capture.req.hdr(1)] entspricht User-Agent
   log-format '{"client_ip":"%ci","timestamp":"%t","method":"%HM","path":"%HP","proto":"%HV","status":%ST,"bytes_read":%B,"duration_ms":%Tr,"termination_state":"%ts","trace_id":"%[capture.req.hdr(0)]","user_agent":"%[capture.req.hdr(1)]"}'


   http-request set-header X-Real-IP %[src]  
  
   # ACLs  
   acl host_api       hdr(host) -i agt-3.agt-app.de  
   acl host_client    hdr(host) -i client.agt-app.de  
   acl host_website   hdr(host) -i agt-app.de  
  
   acl path_sync_api     path_beg /v3/sync-api  
   acl path_report_api   path_beg /v3/report  
   acl path_primaer_api  path_beg /v3/pri-api  
   acl path_get_api      path_beg /v3/agt-get-api  
   acl path_dashboard    path_beg /dashboard  
  
   # Routing  
   use_backend dashboard_backend_apache if host_website path_dashboard
   use_backend website_backend          if host_website  
   use_backend api_backend_sync         if host_api path_sync_api  
   use_backend api_backend_report       if host_api path_report_api  
   use_backend api_backend_primaer      if host_api path_primaer_api  
   use_backend api_backend_get          if host_api path_get_api   
   use_backend client_backend_apache    if host_client  
  
   default_backend backend_404_error  

# ----------------------------------------------------------------------  
# Backends mit Failover (Active-Passive)  
# ----------------------------------------------------------------------  

# Website agt-app.de  
backend website_backend  
   balance roundrobin
   http-request set-path /website%[path]  
   option httpchk GET /index.html  
   server agt-1 172.20.0.1:3102 check    
   server agt-2 172.20.0.2:3102 check   
   server agt-3 172.20.0.3:3102 check   

backend api_backend_sync  
   option forwardfor
   option httpchk GET /v3/sync-api/health
   balance leastconn
   server agt-1 172.20.0.1:3111 check  
   server agt-2 172.20.0.2:3111 check 
   server agt-3 172.20.0.3:3111 check

backend api_backend_report  
   option forwardfor
   option httpchk GET /v3/report/health  
   balance leastconn
   server agt-1 172.20.0.1:3112 check  
   server agt-2 172.20.0.2:3112 check   
   server agt-3 172.20.0.3:3112 check   

backend api_backend_primaer  
   option forwardfor
   option httpchk GET /v3/pri-api/health
   balance leastconn  
   server agt-1 172.20.0.1:3113 check 
   server agt-2 172.20.0.2:3113 check  
   server agt-3 172.20.0.3:3113 check  

backend api_backend_get  
   option forwardfor
   option httpchk GET /v3/agt-get-api/health  
   balance leastconn
   server agt-1 172.20.0.1:3114 check  
   server agt-2 172.20.0.2:3114 check   
   server agt-3 172.20.0.3:3114 check   

backend client_backend_apache  
   option forwardfor
   option httpchk GET /index.html
   balance roundrobin  
   server agt-1 172.20.0.1:3101 check  
   server agt-2 172.20.0.2:3101 check   
   server agt-3 172.20.0.3:3101 check   

backend dashboard_backend_apache  
   #http-request set-path %[path,regsub(^/dashboard/,/)] if { path_beg /dashboard/ }  
   option forwardfor
   option httpchk GET /index.html 
   balance roundrobin 
   server agt-1 172.20.0.1:3102 check  
   server agt-2 172.20.0.2:3102 check   
   server agt-3 172.20.0.3:3102 check   

backend coraza-spoa
    mode tcp
    # Docker DNS Name nutzen
    server s1 coraza-spoa:9000 check 

backend backend_404_error  
   errorfile 404 /usr/local/etc/haproxy/errors/404.http
   

````

Bei berechtigungsproblemen chmod 644 hilft oft...

```bash

# hier müssen jetzt einmal alle zertifikate auf alle server kopiert werden
# danach starten.

docker compose up -d
nano crontab -e

#add
0 0 * * * /root/deploy_haproxy_certs.sh > /var/log/haproxy_deploy.log 2>&1

```



## Erweitertes Monitoring

