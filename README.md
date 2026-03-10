# HAProxy + Geo-Blocking + Staged Rollout (BOS)

Einheitliches Docker-Setup für HAProxy 3+ mit Coraza WAF und Geo-Manager (Safety Pipeline, Staged Rollout). Ein Repo, eine `docker-compose.yaml`; die Differenzierung pro Server erfolgt nur über die `.env`.

**Für KI-Agenten und Weiterentwicklung**: Siehe [AGENTS.md](AGENTS.md) (Architektur, Konventionen, Erweiterung, Tests).

## Komponenten

- **HAProxy 3.2**: Loadbalancer, TLS, Geo-IP-ACLs (Maps), SPOE für WAF
- **Coraza SPOA**: WAF-Agent (OWASP CRS)
- **Geo-Manager**: Sidecar für Geo-IP-Maps: Download, Validierung (Syntax, Größe, Anchor-Check), Staged Rollout (Prio 1 sofort, Prio 2 nach 48h, Prio 3 nach 96h)

## Voraussetzungen

- Docker & Docker Compose
- Pro Server: eigene `.env` mit `NODE_NAME`, `NODE_PRIO`, `MESH_NODES`, `ANCHOR_IPS`, `GEO_SOURCE_URL`
- SSL: `ssl/haproxy.pem` (Fullchain + Privkey) pro Server
- Coraza-Regeln: Submodule unter `coraza/rules/coreruleset/` (nach Klonen: `git submodule update --init --recursive`)

## Deployment

1. Repo klonen, in das Verzeichnis wechseln.
2. `.env` anlegen: Pro Server die passende Konfiguration verwenden. Im Repo können z. B. `1.env`, `2.env`, `3.env` als Vorlagen pro Knoten liegen; auf dem Server wird die jeweilige Datei als `.env` abgelegt (z. B. `1.env` → `.env` auf agt-1). Inhalt pro Server anpassen:
   - **AGT-1**: `NODE_NAME=agt-1`, `NODE_PRIO=1`
   - **AGT-2**: `NODE_NAME=agt-2`, `NODE_PRIO=2`
   - **AGT-3**: `NODE_NAME=agt-3`, `NODE_PRIO=3`
   - `MESH_NODES` = WireGuard-IPs aller drei (z. B. `172.20.0.1,172.20.0.2,172.20.0.3`)
   - `ANCHOR_IPS` = Komma-getrennte IPs, die in der Geo-Liste als DE/EU gelten müssen (Plausibilitäts-Check).
   - `GEO_SOURCE_URL` = URL zur Geo-IP-CSV (oder `GEO_BLOCKS_URL` + `GEO_LOCATIONS_URL` für MaxMind-Style).
3. **Zertifikate:** Entweder `ssl/haproxy.pem` manuell bereitstellen **oder** cert-manager nutzen (Let's Encrypt/Certbot). Bei Certbot: In der `.env` `CERT_LE_BASE_HOST=/etc/letsencrypt` setzen (nicht nur `live/domain`), damit die Symlinks in `live/<domain>/` auf `archive/<domain>/` im Container auflösen. Zusätzlich `CERT_SOURCE_FULLCHAIN=/certs/live/<domain>/fullchain.pem` und `CERT_SOURCE_PRIVKEY=/certs/live/<domain>/privkey.pem` (z. B. `agt-app.de`).
4. Coraza-Regeln: Submodule initialisieren: `git submodule update --init --recursive` (siehe `coraza/rules/README.md`).
5. Start: `docker compose up -d` (Compose lädt die `.env` im Projektordner automatisch).

Auf jedem der drei Server denselben Ablauf mit jeweils passender `.env` ausführen. Es wird nur eine `docker-compose.yaml` verwendet.

## Lokaler Test (Laptop)

Zum Durchspielen des kompletten Ablaufs (Download → Validierung → Umbau/Reload) lokal:

**Voraussetzungen:** Coraza-Regeln via Submodule (`git submodule update --init --recursive`). SSL + Socket: für lokalen Test einmal `./scripts/gen-dev-cert.sh` ausführen (erzeugt `ssl/haproxy.pem` und bereitet `run/haproxy-stat` mit Rechten für HAProxy vor). Wenn HAProxy im Loop abstürzt („Restarting“): `sudo chown 99:99 run/haproxy-stat` ausführen.

1. **Geo-CSV einmalig auf dem Host laden** (im Container oft kein Internet):
   ```bash
   curl -o conf/test-data/geoip2-ipv4.csv https://raw.githubusercontent.com/datasets/geoip2-ipv4/main/data/geoip2-ipv4.csv
   ```

2. **`.env` für lokalen Lauf anlegen**
   ```bash
   cp .env.local.example .env
   ```
   Darin steht `GEO_SOURCE_URL=file:///data/geoip2-ipv4.csv` – die Datei wird aus dem gemounteten Ordner `conf/test-data` gelesen.

3. **Stack starten**
   ```bash
   docker compose up --build -d
   ```

4. **Ablauf beobachten**
   - Geo-Manager (Master) lädt die CSV, baut die Maps und triggert den HAProxy-Reload (erster Lauf kann etwas dauern).
   - Logs: `docker compose logs -f geo-manager`
   - Status: `curl -s http://localhost:8080/geo/status`
   - Danach: `conf/maps/geo.map` und `conf/maps/whitelist.map` sind aktualisiert.

**Optional:** Eigene Test-CSV lokal bereitstellen: `GEO_SOURCE_URL=http://host.docker.internal:8000/geo-sample.csv` setzen und in einem Terminal `cd conf/test-data && python3 -m http.server 8000` starten.

## Geo-Manager

- **Master (Prio 1)**: Lädt periodisch die Geo-Quelle (einstellbar via `FETCH_INTERVAL_HOURS`), prüft Syntax (haproxy -c), Größe und Anchor-IPs, schreibt Maps und löst Reload aus. Setzt danach `validated_at`. Bei Fehlschlag: konfigurierbare Retries mit Wartezeit, danach optional Mail-Benachrichtigung (z. B. mailcow).
- **Follower (Prio 2/3)**: Fragen den Master per HTTP (`/geo/status`) ab und übernehmen eine neue Map erst, wenn sie beim Master seit 48h (Prio 2) bzw. 96h (Prio 3) fehlerfrei aktiv ist. Ebenfalls Retries und Mail bei anhaltendem Fehlschlag.
- **Cluster-Health**: Wöchentlich (oder per `CLUSTER_HEALTH_INTERVAL_HOURS`) werden alle Mesh-Knoten angefragt; Latenz und Offline-Phasen werden gespeichert und über `/cluster` bzw. `/metrics` (Prometheus) bereitgestellt.

**HTTP-Endpunkte** (Port 8080, konfigurierbar mit `GEO_STATUS_PORT`):

| Pfad | Beschreibung |
|------|--------------|
| `GET /health` | Einfacher Liveness-Check (200 OK, Body „OK“) – z. B. für Load-Balancer oder Monitoring. |
| `GET /geo/status` | JSON: `node_prio`, `validated_at`, `map_version`. |
| `GET /cluster` | JSON: letzter Cluster-Probe-Stand (Knoten, Latenz, Offline-Zusammenfassung). |
| `GET /metrics` | Prometheus-Text-Format (Cluster-Erreichbarkeit, Latenz, Zeitstempel). |

**Stabilität:** Kein Absturz bei Netzausfall oder Mail-/SMTP-Fehlern; Retries mit Wartezeit vor Benachrichtigung. Optional IPv6-Unterstützung über `GEO_BLOCKS_IPV6_URL` (Zwei-Dateien-Quelle).

## Tests

```bash
cd geo-manager
pip install -r requirements-dev.txt  # oder: pytest pytest-cov
pytest tests/ -v --cov=geo_manager --cov-fail-under=100 --cov-report=term-missing
```

## Lizenz / Hinweis

BOS-relevant; Konfiguration und Anpassungen gemäß interner Vorgaben vornehmen.
