# HAProxy-Integrationstests

Docker-basierte End-to-End-Tests für die HAProxy-Konfiguration. Die Tests prüfen das Zusammenspiel von HAProxy, Coraza WAF und den Sicherheitsmechanismen (Rate-Limiting, Geo-Blocking, Überlastungsschutz) gegen die echte Produktionskonfiguration.

---

## Schnellstart

```bash
# Alle Tests ausführen (Standard-Output)
./scripts/run-haproxy-tests.sh

# Nur bestimmte Tests
./scripts/run-haproxy-tests.sh -k test_waf -v
./scripts/run-haproxy-tests.sh -k test_rate_limiting
./scripts/run-haproxy-tests.sh -k "test_geo and not reload"

# Stopp beim ersten Fehler
PYTEST_ARGS="-x -v --tb=long" ./scripts/run-haproxy-tests.sh
```

**Voraussetzungen:** Docker (Compose v2), openssl. Laufzeit ca. 30–60 Sekunden (exkl. Docker-Build beim ersten Mal).

---

## Architektur

Die Testumgebung besteht aus vier Docker-Containern, orchestriert über `tests/haproxy/docker-compose.test.yaml`:

```
┌──────────────┐     HTTPS :443      ┌──────────────┐     SPOE :9000     ┌──────────────┐
│  test-runner  │ ──────────────────▶ │   haproxy    │ ─────────────────▶ │  coraza-spoa │
│  (pytest)     │     HTTP :80        │ (Prod-Config) │                    │ (OWASP CRS)  │
└──────────────┘                      └──────┬───────┘                    └──────────────┘
       │                                     │ Routing
       │ Stats-Socket                        ▼
       │ (Unix)                       ┌──────────────┐
       └────────────────────────────▶ │ dummy-backend │
                                      │ (Python, 6×)  │
                                      │ Ports 3101-3114│
                                      └──────────────┘
```

| Service | Image | Funktion |
|---------|-------|----------|
| **haproxy** | `haproxy:3.2-alpine` (Prod-Dockerfile) | Echte Konfiguration (`conf/conf.d/`, Maps, Errors, Entrypoint). Selbst-signiertes Testzertifikat. Single-Node-Modus (`NODE_NAME=agt-1`). |
| **coraza-spoa** | Prod-Build (`coraza/Dockerfile.coraza`) | Echte WAF-Config mit OWASP CRS. Nötig für WAF-Tests. |
| **dummy-backend** | `python:3.11-alpine` | Python-HTTP-Server auf allen Backend-Ports (3101, 3102, 3111–3114). Liefert Health-Check-Antworten und identifizierbare Responses. Unterstützt `/slow`-Endpoint für Verbindungslimit-Tests. |
| **test-runner** | `python:3.11-alpine` + pytest/requests/socat | Führt pytest aus. Kommuniziert mit HAProxy über HTTPS/HTTP und den Stats-Socket (Unix-Socket, geteiltes Volume). |

### Designprinzipien

- **Echte Konfiguration**: Die Tests nutzen die Produktions-HAProxy-Config (nicht Kopien). Änderungen an `conf/conf.d/`, Maps oder Errors werden automatisch mitgetestet.
- **Kein Mock**: Coraza läuft als echter SPOE-Agent mit OWASP CRS.
- **Deterministisch**: Alle Stick-Tables werden vor jedem Test geleert (autouse-Fixture). Map-Werte werden nach Tests, die sie verändern, wiederhergestellt.
- **Isoliert**: SSL-Zertifikat und Maps werden in Temp-Verzeichnisse kopiert; das Repo bleibt unberührt.

---

## Testfälle (38 Tests in 7 Dateien)

### Routing und Fehlerseiten (`test_routing.py`, 11 Tests)

| Test | Prüft |
|------|-------|
| `test_http_redirect_to_https` | Port 80 liefert 301-Redirect auf HTTPS für erlaubte IPs. |
| `test_unknown_host_returns_404` | Unbekannter Host → `backend_404_error` → 404. |
| `test_api_sync_route` | `agt-1.agt-app.de/v3/sync-api/…` → `api_backend_sync` (Port 3111). |
| `test_api_report_route` | `agt-1.agt-app.de/v3/report/…` → `api_backend_report` (Port 3112). |
| `test_api_primaer_route` | `agt-1.agt-app.de/v3/pri-api/…` → `api_backend_primaer` (Port 3113). |
| `test_api_get_route` | `agt-1.agt-app.de/v3/agt-get-api/…` → `api_backend_get` (Port 3114). |
| `test_website_route` | `agt-app.de/` → `website_backend` (Port 3102). |
| `test_dashboard_route` | `agt-app.de/dashboard` → `dashboard_backend_apache` (längster `map_beg`-Prefix). |
| `test_client_route` | `client.agt-app.de/` → `client_backend_apache` (Port 3101). |
| `test_api_host_without_path_404` | API-Host ohne bekannten Pfad → 404. |
| `test_all_three_api_hosts_work` | Alle drei API-Hostnamen (`agt-1/2/3.agt-app.de`) routen korrekt. |

### Per-IP Rate-Limiting (`test_rate_limiting.py`, 7 Tests)

Testet die Stick-Table-basierten Limits aus `conf/maps/rate-limits.map` (sc2-Slot, pro IP mit `ipmask(32,48)`).

| Test | Prüft |
|------|-------|
| `test_api_get_rate_limit` | 20 Requests erlaubt, 21. → 429 (`api_get` Limit: 20 / 300 s). |
| `test_api_report_rate_limit` | 10 Requests erlaubt, 11. → 429 (`api_report` Limit: 10 / 60 s). |
| `test_api_primaer_verify_rate_limit` | 20 Requests erlaubt, 21. → 429 (`api_primaer_verify` Limit: 20 / 600 s). |
| `test_rate_limit_response_format` | 429-Antwort hat `Content-Type: application/json`, Body mit `error: rate_limit_exceeded`. |
| `test_different_endpoints_independent` | Ausschöpfung des Report-Limits beeinflusst Get-API nicht. |
| `test_rate_limit_with_lowered_value` | Runtime-API `set map` kann Limits zur Laufzeit senken. |
| `test_website_rate_limit_high_threshold` | Website-Limit (2000 req/s) wird durch kleine Bursts nicht erreicht. |

### Backend-Überlastungsschutz (`test_overload.py`, 3 Tests)

Testet den globalen Overload-Schutz (sc3-Slot, `st_overload`, `overload-limits.map`). Limits werden per Runtime-API temporär gesenkt, um den Test zu ermöglichen.

| Test | Prüft |
|------|-------|
| `test_overload_503_response` | Überlastung → 503 mit JSON-Body `backend_overload` und `Retry-After: 5`. |
| `test_overload_different_backends_independent` | Überlastung eines Backends blockiert andere nicht. |
| `test_overload_recovery` | Nach Ablauf des 1-s-Fensters erholt sich das Backend (200 statt 503). |

### Coraza WAF (`test_waf.py`, 7 Tests)

Testet die SPOE-Integration, OWASP CRS und die Auto-Ban-Logik.

| Test | Prüft |
|------|-------|
| `test_waf_test_rule_blocks` | `?testwaf=123` → 403 (Coraza Test-Rule `id:190001`). |
| `test_waf_normal_request_passes` | Sauberer Request → 200 (kein WAF-Block). |
| `test_waf_sql_injection_blocked` | SQL-Injection-Payload (`UNION SELECT …`) → 403. |
| `test_waf_xss_blocked` | XSS-Payload (`<script>…</script>`) → 403. |
| `test_waf_rce_blocked` | Remote-Code-Execution-Payload (`; cat /etc/passwd`) → 403. |
| `test_internal_ip_not_auto_banned` | Docker-IPs (172.x, `internal_networks`) werden nach WAF-Deny nicht auto-gebannt. Nächster sauberer Request geht durch. |
| `test_waf_ban_check_via_stick_table` | Manuell injizierter `gpc0`-Wert in `st_waf_blocks` wird korrekt gespeichert. |

**Hinweis zu `internal_networks`:** Der Test-Runner hat eine Docker-IP im 172.x-Bereich, die unter die `internal_networks`-ACL fällt. Die WAF-Deny-Aktion (Coraza SPOE) funktioniert für alle IPs. Der Auto-Ban (`gpc0` in `st_waf_blocks`) greift jedoch nur für externe IPs – dieses Verhalten wird explizit getestet.

### Geo-Blocking und Map-Reload (`test_geo.py`, 5 Tests)

Testet Geo-IP-Filtering und unterbrechungsfreie Map-Updates via HAProxy Runtime-API.

| Test | Prüft |
|------|-------|
| `test_geo_default_allows_all` | Default-`geo.map` (`0.0.0.0/0 → DE`) erlaubt alle Requests. |
| `test_geo_blocked_country_gets_403` | Nach Map-Update (`XX` statt `DE`) und Whitelist-Clearing → 403 mit Geo-Block-Seite. |
| `test_geo_whitelist_bypasses_block` | IP in `whitelist.map` umgeht Geo-Block trotz Ländersperre. |
| `test_http_frontend_geo_block` | Port 80 liefert 403-geo statt 301-Redirect bei geblocktem Land. |
| `test_geo_map_reload_no_downtime` | Parallele Map-Updates via `set map` verursachen keine 5xx-Fehler für laufende Requests. |

### SSL-Zertifikat-Reload (`test_cert_reload.py`, 2 Tests)

Testet Zero-Downtime-Zertifikatswechsel über `set ssl cert` + `commit ssl cert` (HAProxy Runtime-API).

| Test | Prüft |
|------|-------|
| `test_cert_content_updated` | Nach Runtime-API-Update liefert HAProxy das neue Zertifikat (DER-Vergleich). |
| `test_cert_reload_no_downtime` | Während 3 aufeinanderfolgender Cert-Swaps laufen parallele HTTPS-Requests fehlerfrei. |

### Cluster-Verbindungslimit (`test_cluster_limit.py`, 3 Tests)

Testet das clusterweite Verbindungslimit (`CLUSTER_MAXCONN`, `st_global_conn`, `conn_cur`). Die Testumgebung setzt `CLUSTER_MAXCONN=5`. Requests an den `/slow`-Endpoint (3 s Antwortzeit) halten Verbindungen lange genug offen, um das Limit zu triggern.

| Test | Prüft |
|------|-------|
| `test_cluster_maxconn_503` | Bei >= `CLUSTER_MAXCONN` gleichzeitigen HTTPS-Verbindungen → 503. |
| `test_cluster_maxconn_on_http` | Gleiches Verhalten auf Port 80 (gemeinsame `st_global_conn`). |
| `test_connections_recover_after_slow` | Nach Schließen der Verbindungen erholt sich das Limit (200 statt 503). |

---

## Dateistruktur

```
tests/haproxy/
├── docker-compose.test.yaml      # Docker-Compose-Definition der Testumgebung
├── Dockerfile.test-runner         # Image: Python 3.11 + pytest + requests + socat + openssl
├── requirements.txt               # Python-Abhängigkeiten (pytest, requests, urllib3)
├── conftest.py                    # Shared Fixtures, HAProxy-API-Helpers, Map/Table-Reset
├── test_routing.py                # Routing- und Fehlerseiten-Tests
├── test_rate_limiting.py          # Per-IP Rate-Limit-Tests
├── test_overload.py               # Backend-Überlastungsschutz-Tests
├── test_waf.py                    # Coraza WAF-Tests
├── test_geo.py                    # Geo-Blocking- und Map-Reload-Tests
├── test_cert_reload.py            # SSL-Zertifikat-Reload-Tests
├── test_cluster_limit.py          # Cluster-Verbindungslimit-Tests
└── fixtures/
    └── dummy_backend.py           # Python-HTTP-Server (Ports 3101–3114, inkl. /slow)

scripts/
└── run-haproxy-tests.sh           # Runner: Cert-Generierung, Maps-Kopie, Compose up/down
```

---

## Ablauf des Runner-Skripts

`scripts/run-haproxy-tests.sh` führt folgende Schritte aus:

1. **Temp-Verzeichnisse** erstellen für SSL-Zertifikat und Maps-Kopie (Repo bleibt unberührt).
2. **Selbst-signiertes Zertifikat** generieren (`openssl req -x509 …` → `haproxy.pem`).
3. **Maps kopieren** aus `conf/maps/` in das Temp-Verzeichnis.
4. **Docker Compose starten** (`--build --abort-on-container-exit --exit-code-from test-runner`).
5. **Exit-Code** des test-runner Containers als Skript-Exit-Code durchreichen.
6. **Aufräumen** (Trap): `docker compose down -v`, Temp-Verzeichnisse löschen.

---

## Fixtures und Helpers (`conftest.py`)

### HAProxy Runtime-API

Die Tests kommunizieren mit HAProxy über den Stats-Socket (`/var/run/haproxy-stat/socket`, Level admin). Der Socket wird als Named Volume zwischen HAProxy und Test-Runner geteilt.

| Funktion | Beschreibung |
|----------|--------------|
| `haproxy_cmd(cmd)` | Sendet ein Kommando an den Stats-Socket und gibt die Antwort zurück. |
| `clear_table(name)` | Leert eine Stick-Table (`clear table <name>`). |
| `set_map(path, key, val)` | Ändert einen Map-Eintrag im Speicher (`set map`). |
| `add_map(path, key, val)` | Fügt einen Map-Eintrag hinzu (`add map`). |
| `clear_map(path)` | Leert eine Map komplett (`clear map`). |
| `restore_map(path, defaults)` | Leert und baut eine Map aus Defaults neu auf. |
| `restore_rate_limits()` | Stellt `rate-limits.map` auf Produktionswerte zurück. |
| `restore_overload_limits()` | Stellt `overload-limits.map` auf Produktionswerte zurück. |

### SSL-Helpers

| Funktion | Beschreibung |
|----------|--------------|
| `get_server_cert_der(host, port)` | Verbindet via TLS und gibt das Serverzertifikat als DER-Bytes zurück. |
| `generate_self_signed_pem(cn)` | Erzeugt ein selbst-signiertes PEM (Cert + Key) mit `openssl`. |
| `update_ssl_cert(cert_path, pem)` | Aktualisiert ein Zertifikat via Runtime-API (`set ssl cert` + `commit ssl cert`). |

### Autouse-Fixtures

| Fixture | Scope | Beschreibung |
|---------|-------|--------------|
| `wait_for_haproxy` | Session | Wartet bis zu 90 s, bis HAProxy HTTPS-Requests beantwortet und der Stats-Socket erreichbar ist. |
| `reset_tables` | Function | Leert alle 11 Stick-Tables vor jedem Test für deterministischen State. |

---

## Erweiterung: Neuen Test hinzufügen

1. **Neue Testdatei** `tests/haproxy/test_feature.py` anlegen oder bestehende Datei erweitern.
2. **Fixtures** aus `conftest.py` nutzen: `base_url`, `http_url` für Requests; `haproxy_cmd()` für Runtime-API.
3. **State aufräumen**: Falls Maps oder Limits verändert werden, im `finally`-Block oder per Fixture wiederherstellen.
4. **Lokal testen**: `./scripts/run-haproxy-tests.sh -k test_feature -v`
5. **CI**: Der Job `haproxy-integration` in `.github/workflows/ci.yml` führt alle Tests automatisch aus.

### Beispiel

```python
"""test_feature.py – Neues Feature testen."""
import requests
from conftest import set_map, RATE_LIMITS_MAP, restore_rate_limits

def test_my_new_feature(base_url):
    """Beschreibung was getestet wird."""
    try:
        set_map(RATE_LIMITS_MAP, "api_get", "3")
        r = requests.get(
            f"{base_url}/v3/agt-get-api/test",
            headers={"Host": "agt-1.agt-app.de"},
            verify=False, timeout=5,
        )
        assert r.status_code == 200
    finally:
        restore_rate_limits()
```

---

## CI-Integration

Der CI-Workflow (`.github/workflows/ci.yml`) enthält drei Jobs:

| Job | Was |
|-----|-----|
| `test` | Geo-Manager Unit-Tests mit 100 % Coverage. |
| `docker` | Docker-Build-Smoke-Test (geo-manager + Coraza). |
| `haproxy-integration` | HAProxy-Integrationstests via `./scripts/run-haproxy-tests.sh`. |

Alle drei Jobs laufen bei Push/PR auf `main`/`master`.

---

## Bekannte Einschränkungen

- **internal_networks**: Der Test-Runner hat eine Docker-IP (172.x), die in der `internal_networks`-ACL liegt. WAF Auto-Ban (`gpc0` in `st_waf_blocks`) greift dadurch nicht für den Test-Runner. Die WAF-Deny-Aktion selbst funktioniert aber korrekt. Das Verhalten wird explizit getestet (`test_internal_ip_not_auto_banned`).
- **Geo-Blocking + Whitelist**: Die Standard-`whitelist.map` enthält 172.16.0.0/12. Um Geo-Blocking zu testen, muss die Whitelist via Runtime-API geleert werden. Tests, die Maps verändern, stellen diese im `finally`-Block wieder her.
- **Cluster-Verbindungslimit**: Getestet mit dem `/slow`-Endpoint des Dummy-Backends (3 s Antwortzeit). Die Zuverlässigkeit hängt davon ab, dass Verbindungen während der Slow-Response aktiv bleiben.
- **Peer-Sync**: Im Single-Node-Testmodus gibt es keine Peer-Synchronisation. Die Stick-Tables arbeiten rein lokal. Cluster-übergreifende Sync-Szenarien sind damit nicht abgedeckt.
