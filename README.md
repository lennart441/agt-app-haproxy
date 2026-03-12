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

**Stabilität:** Kein Absturz bei Netzausfall oder Mail-/SMTP-Fehlern; Retries mit Wartezeit vor Benachrichtigung. Optional IPv6-Unterstützung über `GEO_BLOCKS_IPV6_URL` (Zwei-Dateien-Quelle).

---

## Ports und Endpunkte (Referenz)

Übersicht aller Ports und HTTP-Pfade: wofür sie da sind, wer sie nutzt und wie man sie einsetzt.

### Ports auf dem Host (Docker-Publish)

| Port | Dienst | Zweck |
|------|--------|--------|
| **80** | HAProxy | HTTP – Redirect auf HTTPS (nur nach Geo/Whitelist-Check). Andernfalls 403 (Geo-Block). |
| **443** | HAProxy | HTTPS – Einstieg für alle Anwendungen (API, Website, Client, Dashboard). Geo-Check, WAF, Rate-Limits. |
| **50000** | HAProxy | Peers – Cluster-Sync der Stick-Tables (WAF Auto-Ban, Rate-Limits, Verbindungszähler). Nur im Mesh erreichbar halten. |
| **8404** | HAProxy | Prometheus-Metrics – `GET /metrics` im Prometheus-Text-Format (HAProxy-Metriken). Für Monitoring. |
| **8080** | Geo-Manager | Status/Health/Cluster/Metrics und manueller Deploy-Trigger. Für Follower-Abfrage und Ops. |
| **8081** | Cert-Manager | Zertifikats-Status, Download, Dashboard und Deploy-Trigger. Für Follower und Ops-Dashboard. |

### Geo-Manager (Port 8080, ENV: `GEO_STATUS_PORT`)

| Methode + Pfad | Beschreibung | Wofür nötig |
|----------------|--------------|-------------|
| `GET /health` | Liveness (200 OK, Body „OK“). | Load-Balancer, Docker-Healthcheck, Kubernetes Liveness. |
| `GET /geo/status` | JSON: `node_name`, `node_prio`, `validated_at`, `map_version`, ggf. weitere Felder. | Follower prüfen, ob Master eine gültige Map hat; Staged Rollout (48h/96h). |
| `GET /cluster` | JSON: letzter Cluster-Probe-Stand (Knoten, Latenz, Offline-Infos). | Ops: Übersicht, ob alle Knoten im Mesh erreichbar sind. |
| `GET /metrics` | Prometheus-Text-Format (Geo-Manager-Metriken + Cluster-Health). | Prometheus/Grafana: Fetch-Erfolge, Validierung, Reload, Cluster-Latenz. |
| `POST /geo/deploy-now` | Triggert sofortigen Geo-Download/Validierung/Aktivierung (nur Master). Follower: 403. | Manueller Rollout ohne Warten auf `FETCH_INTERVAL_HOURS`. |

**Beispiel:** Status vom lokalen Geo-Manager abfragen: `curl -s http://localhost:8080/geo/status`

### Cert-Manager (Port 8081, ENV: `CERT_STATUS_PORT`)

| Methode + Pfad | Beschreibung | Wofür nötig |
|----------------|--------------|-------------|
| `GET /health` | Liveness (200 OK, Body „OK“). | Docker-Healthcheck, Liveness-Probes. |
| `GET /cert/status` | JSON: `node_name`, `node_prio`, `cert_is_master`, `version`, `validated_since`. Optional Query: `?cluster_key=…` (wenn `CERT_CLUSTER_KEY` gesetzt). | Follower ermitteln Master und ob neues Zertifikat übernommen werden soll. |
| `GET /cert/download?version=…&cluster_key=…` | Liefert das aktuelle PEM (Fullchain+Privkey). Query-Parameter nötig bei gesetztem `CERT_CLUSTER_KEY`. | Follower laden vom Master das PEM für Staged Rollout. |
| `GET /dashboard` | HTML-Dashboard: aggregierter Status aller Knoten (Geo + Cert), Links zu Deploy-Buttons. | Ops: einheitliche Übersicht aller Knoten; Deploy-Trigger im Browser. |
| `POST /cert/deploy-now` | Triggert sofortigen Zertifikats-Rollout (Master schreibt PEM, Follower holen es nach Delay). | Nach Certbot-Renewal: Zertifikat ohne Warten im Cluster verteilen. |
| `POST /geo/deploy-now` | Leitet an Geo-Manager weiter (POST an `geo-manager:8080/geo/deploy-now`). | Vom Dashboard aus: einen Klick für „Geo jetzt aktualisieren“. |

**Hinweis:** `/cert/status` und `/cert/download` sind optional mit `cluster_key` geschützt (`CERT_CLUSTER_KEY`); ohne Key sind sie für alle erreichbar (z. B. nur im Mesh nutzen).

### HAProxy – Öffentliche Pfade (Frontend 80/443)

- **80:** Keine Pfad-Logik; nur Geo/Whitelist → Redirect 301 auf HTTPS, sonst 403 (Geo).
- **443:** Host-basiertes Routing (Beispiele aus `conf/haproxy.cfg`):

| Host (Beispiel) | Pfad | Backend | Zweck |
|-----------------|------|---------|--------|
| `agt-app.de` | `/dashboard*` | dashboard_backend_apache (Port 3102) | Ops-Dashboard (Apache o. Ä.). |
| `agt-app.de` | sonstige | website_backend (3102) | Öffentliche Website. |
| `client.agt-app.de` | / | client_backend_apache (3101) | Client-Anwendung. |
| `agt-1/2/3.agt-app.de` | `/v3/sync-api*` | api_backend_sync (3111) | Sync-API. |
| `agt-1/2/3.agt-app.de` | `/v3/report*` | api_backend_report (3112) | Report-API. |
| `agt-1/2/3.agt-app.de` | `/v3/pri-api*` | api_backend_primaer (3113) | Primär-API. |
| `agt-1/2/3.agt-app.de` | `/v3/agt-get-api*` | api_backend_get (3114) | AGT-Get-API. |

Health-Checks (intern): z. B. `GET /v3/sync-api/ready`, `GET /v3/report/ready`, `GET /v3/pri-api/ready`, `GET /v3/agt-get-api/ready`.

### HAProxy – Nur intern / Monitoring

| Zugang | Port/Bind | Pfad | Zweck |
|--------|-----------|------|--------|
| Stats-UI | 127.0.0.1:56708 (im Container) | `/kM3liYHB` | HAProxy-Statistik, Admin (Auth: `STATS_USER`/`STATS_PASSWORD`). Nicht nach außen binden. |
| Prometheus | 8404 (Host) | `GET /metrics` | HAProxy-Metriken für Prometheus. |

**Hinweis:** Die Stats-UI ist nur über `127.0.0.1` im HAProxy-Container erreichbar; für Zugriff vom Host ggf. `docker exec` oder separates Port-Mapping mit Vorsicht (nur lokal).

### Interne Ports (nur im Docker-Netz / Mesh)

- **Coraza SPOA:** 9000 (HAProxy verbindet als Backend `coraza-spoa:9000`) – WAF-Anfragen.
- **Backend-Server (Beispiele):** 3101 (Client), 3102 (Website/Dashboard), 3111–3114 (APIs) – das sind die Mesh-IPs der anderen Knoten bzw. Backend-Services laut `conf/haproxy.cfg`.
- **Peers 50000:** Kommunikation zwischen HAProxy-Knoten für Stick-Tables; nur zwischen den drei Knoten (z. B. WireGuard-Mesh) erreichbar halten.

## Tests

```bash
cd geo-manager
pip install -r requirements-dev.txt  # oder: pytest pytest-cov
pytest tests/ -v --cov=geo_manager --cov-fail-under=100 --cov-report=term-missing
```

## Lizenz / Hinweis

BOS-relevant; Konfiguration und Anpassungen gemäß interner Vorgaben vornehmen.
