# HAProxy Konfigurationsstruktur

## Verzeichnisaufbau

```
conf/
├── conf.d/                          Modulare HAProxy-Konfiguration
│   ├── 00-global.cfg                Globale Einstellungen (Logging, Socket, Threads)
│   ├── 10-peers.cfg                 Cluster-Sync (Stick-Tables über WireGuard-Mesh)
│   ├── 15-resolvers.cfg             Docker DNS-Resolver
│   ├── 20-defaults.cfg              Defaults (Timeouts, Error-Files)
│   ├── 30-stick-tables.cfg          Stick-Table-Backends (Rate-Limits, WAF, Conn-Limit)
│   ├── 35-error-backends.cfg        Fehler-Backends (403-geo, 403-waf, 404)
│   ├── 40-frontend-http.cfg         HTTP → HTTPS Redirect + Geo-Check
│   ├── 45-frontend-stats.cfg        Stats-Dashboard + Prometheus-Exporter
│   ├── 50-frontend-https.cfg        HTTPS-Frontend (WAF, Geo, Rate-Limits, Routing)
│   └── 60-backends.cfg              Applikations-Backends (API, Website, Client)
├── maps/                            Map-Dateien für dynamische Konfiguration
│   ├── geo.map                      Geo-IP-Zuordnung (vom Geo-Manager verwaltet)
│   ├── whitelist.map                IP-Whitelist (RFC1918 + Anchors, Geo-Manager)
│   ├── hosts.map                    Hostname → Typ (api/website/client)
│   ├── routing.map                  Host+Pfad → Backend (map_beg Routing)
│   └── rate-limits.map              Route-ID → max. Requests/Sekunde
├── errors/                          Benutzerdefinierte HTTP-Fehlerseiten
│   ├── 403.http                     Standard 403 Forbidden
│   ├── 403-geo.http                 403 Geo-Block
│   ├── 403-waf.http                 403 WAF-Block
│   ├── 404.http                     404 Not Found
│   ├── 429-rate-limit.http          429 Rate Limit
│   └── 503.http                     503 Service Unavailable
├── coraza.cfg                       SPOE-Konfiguration für Coraza WAF
└── coraza-spoa.yaml                 Coraza SPOA Agent-Config
```

## Ladereihenfolge

HAProxy lädt alle `.cfg`-Dateien aus `conf.d/` in **alphabetischer Reihenfolge**.
Die Nummerierung (00, 10, 15, ...) stellt die korrekte Reihenfolge sicher:

1. **00-global** – Muss zuerst geladen werden (globale Parameter)
2. **10-peers** – Peers-Sektion (vor Stick-Tables, die sie referenzieren)
3. **15-resolvers** – Docker DNS-Resolver
4. **20-defaults** – Defaults für alle Frontends/Backends
5. **30-stick-tables** – Stick-Table-Backends (vor Frontends, die sie referenzieren)
6. **35-error-backends** – Fehler-Backends (vor Frontends, die sie als `use_backend` nutzen)
7. **40-frontend-http** – HTTP-Frontend (Redirect)
8. **45-frontend-stats** – Stats + Prometheus
9. **50-frontend-https** – HTTPS-Frontend (Hauptlogik)
10. **60-backends** – Applikations-Backends

**Wichtig**: `http-request`-Regeln stehen immer **vor** `use_backend` innerhalb eines Frontends.

## Map-Dateien

### hosts.map – Hostname-Klassifizierung

Ordnet Hostnamen einem Typ zu, der für ACLs und Rate-Limiting verwendet wird:

```
agt-1.agt-app.de	api
agt-2.agt-app.de	api
agt-3.agt-app.de	api
agt-app.de	website
client.agt-app.de	client
```

**Neuen Host hinzufügen**: Zeile mit `<hostname>\t<typ>` ergänzen. Typ muss `api`, `website` oder `client` sein (oder neuer Typ mit entsprechendem Rate-Limit und Routing).

### routing.map – Backend-Routing

Bestimmt, welches Backend eine Anfrage verarbeitet. Verwendet `map_beg` (längster Prefix gewinnt):

```
agt-1.agt-app.de/v3/sync-api	api_backend_sync
agt-app.de/dashboard	dashboard_backend_apache
agt-app.de/	website_backend
client.agt-app.de/	client_backend_apache
```

**Neue Route hinzufügen**:
1. Backend in `60-backends.cfg` definieren (falls neu)
2. Zeile in `routing.map`: `<host>/<pfad>\t<backend_name>`
3. Falls Rate-Limiting gewünscht: Stick-Table in `30-stick-tables.cfg`, Track-Regel in `50-frontend-https.cfg`, Eintrag in `rate-limits.map`

### rate-limits.map – Rate-Limit-Schwellenwerte

Definiert max. Requests/Sekunde pro Route-ID:

```
api_get	50
api_sync	200
api_report	25
api_primaer	300
website	2000
client	2000
```

**Rate-Limit ändern**: Wert in der Map anpassen → HAProxy-Reload.

**Neues Rate-Limit hinzufügen**:
1. Stick-Table-Backend in `30-stick-tables.cfg`
2. `track-sc2`- und `set-var(txn.rl_id)`-Regeln in `50-frontend-https.cfg`
3. Eintrag in `rate-limits.map`

## Platzhalter (Template-Variablen)

Die Config-Dateien verwenden Platzhalter, die beim Container-Start durch Umgebungsvariablen
ersetzt werden (via `haproxy-docker-entrypoint.sh`):

| Platzhalter | ENV-Variable | Beschreibung |
|---|---|---|
| `__NODE_NAME__` | `NODE_NAME` | Knoten-Name (agt-1, agt-2, agt-3) |
| `__CLUSTER_MAXCONN__` | `CLUSTER_MAXCONN` | Cluster-weites Verbindungslimit |
| `__STATS_USER__` | `STATS_USER` | Stats-Dashboard Benutzer |
| `__STATS_PASSWORD__` | `STATS_PASSWORD` | Stats-Dashboard Passwort |
| `__GEO_ALLOWED_COUNTRIES_REGEX__` | `GEO_ALLOWED_COUNTRIES` | Erlaubte Länder als Regex |
| `__MESH_IP_1/2/3__` | `MESH_NODES` | WireGuard-Mesh-IPs der Knoten |

## Docker-Integration

```yaml
# docker-compose.yaml (Auszug)
haproxy:
  command: ["haproxy", "-W", "-S", "/var/run/haproxy-stat/master", "-f", "/tmp/conf.d/"]
  volumes:
    - ./conf/conf.d:/usr/local/etc/haproxy/conf.d:ro
    - ./conf/maps:/usr/local/etc/haproxy/maps
    - ./conf/errors:/usr/local/etc/haproxy/errors:ro
```

Der Entrypoint:
1. Liest alle `.cfg`-Dateien aus `/usr/local/etc/haproxy/conf.d/`
2. Ersetzt Platzhalter durch ENV-Werte
3. Schreibt verarbeitete Dateien nach `/tmp/conf.d/`
4. HAProxy lädt `/tmp/conf.d/` (alle Dateien alphabetisch)

## Typische Änderungen

| Aufgabe | Wo |
|---|---|
| Neuer API-Endpunkt | `routing.map` + `60-backends.cfg` |
| Rate-Limit ändern | `rate-limits.map` |
| Neuer Host | `hosts.map` + `routing.map` |
| Timeout ändern | `20-defaults.cfg` |
| SSL-Parameter | `00-global.cfg` oder `50-frontend-https.cfg` |
| WAF-Regeln | `coraza.cfg` / `coraza-spoa.yaml` |
| Fehlerseiten | `errors/*.http` |
