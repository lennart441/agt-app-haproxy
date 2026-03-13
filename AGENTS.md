# Agentenanweisung: agt-app-haproxy

Dieses Dokument ist die zentrale Referenz für KI-Agenten und Entwickler. Es beschreibt Zweck, Architektur, Konventionen und Erweiterung des Projekts.

---

## 1. Projektzweck und Kritikalität

- **Kontext**: Atemschutzüberwachung (BOS), lebens- und einsatzkritisch.
- **Ziel**: Ein einheitliches Docker-Setup (ein Repo, eine `docker-compose.yaml`), das auf allen HAProxy-Knoten mit unterschiedlicher `.env` eingesetzt wird. Kein manuelles Einzel-Setup pro Server.
- **Sicherheitsziel**: Geo-IP-Blocking (nur DE/EU-Grenzregion erlauben). Fehlerhafte Geo-Listen dürfen nie alle Knoten gleichzeitig lahmlegen → Safety Pipeline mit Validierung und Staged Rollout (48h/96h).

---

## 2. Architektur (Überblick)

- **Drei identische Knoten** (stateless), differenziert nur über ENV (`NODE_NAME`, `NODE_PRIO`, `MESH_NODES` usw.). Vernetzung über externes WireGuard-Mesh (z. B. 172.20.0.1–3).
- **Pro Knoten**:
  - **HAProxy 3.2 (Alpine)**: Loadbalancer, TLS, Geo-Maps (`geo.map`, `whitelist.map`), SPOE für WAF.
  - **Coraza SPOA**: WAF-Agent (OWASP CRS), bereits integriert.
  - **Geo-Manager** (Sidecar): Python, Safety Pipeline (Download, Validierung, Staged Rollout), schreibt Maps, triggert HAProxy-Reload.

Detaillierte Architektur und Abläufe stehen im Plan unter `.cursor/plans/` bzw. in der Spezifikation (siehe README).

---

## 3. Verzeichnisstruktur und Verantwortlichkeiten

| Pfad | Inhalt |
|------|--------|
| `conf/` | HAProxy- und WAF-Konfiguration: `conf.d/` (modulare Config), `coraza.cfg`, `coraza-spoa.yaml`, `errors/`, `maps/`. |
| `conf/conf.d/` | Modulare HAProxy-Config (00-global, 10-peers, …, 60-backends); Entrypoint ersetzt Platzhalter und schreibt nach `/tmp/conf.d/`. |
| `conf/maps/` | `geo.map`, `whitelist.map` (Geo-Manager), `hosts.map`, `routing.map`, `rate-limits.map` (Routing/Rate-Limits). |
| `ssl/` | `haproxy.pem` (Fullchain+Privkey), pro Server befüllen, nicht committen. |
| `coraza/` | `Dockerfile.coraza` (Coraza SPOA Build), `rules/coreruleset/` (OWASP CRS als Git-Submodule). |
| `geo-manager/` | Python-Paket `geo_manager`: Config, Fetcher, Validierung, Staging, Reload, HTTP-Status; plus Tests. |
| `geo-manager/geo_manager/` | Quellcode: `config.py`, `fetcher.py`, `validation.py`, `staging.py`, `reload.py`, `main.py`, `__main__.py`. |
| `geo-manager/tests/` | Pytest-Tests; Ziel 100 % Coverage für `geo_manager`. |
| `tests/haproxy/` | Docker-basierte Integrationstests für die HAProxy-Config (Rate-Limits, WAF, Geo, Cert-Reload, Routing). Runner: `scripts/run-haproxy-tests.sh`. |
| `.env.example` | Vorlage für alle ENV-Variablen; pro Server eigene `.env`. |
| `docker-compose.yaml` | Einheitliche Definition für haproxy, coraza-spoa, geo-manager. |
| `Dokumentationen/` | Projekt-Dokumentationen: Installation, Betrieb, Architektur, Wartung. Hier werden alle Doku-Dateien abgelegt (siehe `Dokumentationen/README.md`). |
| `scripts/` | Hilfsskripte: `gen-dev-cert.sh` (lokaler Test: SSL + Socket), `deploy-haproxy-certs.sh` (Let's Encrypt-Zertifikate auf alle Knoten deployen), `run-haproxy-tests.sh` (HAProxy-Integrationstests starten). Konfiguration Deploy: `scripts/cert-deploy.env` (Vorlage: `cert-deploy.env.example`). |

---

## 4. Wichtige ENV-Variablen

- **Identität**: `NODE_NAME` (agt-1/2/3), `NODE_PRIO` (1=Master, 2/3=Follower).
- **Mesh**: `MESH_NODES` (kommaseparierte IPs der anderen Knoten, z. B. WireGuard-IPs).
- **Safety**: `ANCHOR_IPS` (kritische IPs, die in der Geo-Liste als DE/EU gelten müssen), `GEO_SOURCE_URL` (oder `GEO_BLOCKS_URL` + `GEO_LOCATIONS_URL`).
- **Staged Rollout**: `STAGE_DELAY_PRIO2_HOURS=48`, `STAGE_DELAY_PRIO3_HOURS=96`, `SIZE_DEVIATION_THRESHOLD=0.9`.

Weitere Defaults und Erklärungen in `.env.example`.

---

## 5. Geo-Manager: Safety Pipeline (Kurz)

- **Phase A – Leader**: Nur Knoten mit `NODE_PRIO=1` lädt Geo-Daten und schreibt Maps.
- **Phase B – Validierung** (vor Aktivierung): (1) `haproxy -c -f …`, (2) Size-Check gegen vorherige Map-Größe, (3) Anchor-Check (alle `ANCHOR_IPS` müssen erlaubte Länder haben). Bei Fehlschlag: keine Aktivierung.
- **Phase C – Staged Rollout**: Master aktiviert sofort; Follower prüfen `/geo/status` des Masters und übernehmen erst nach 48h bzw. 96h fehlerfreier Laufzeit.

Logik liegt in `geo-manager/geo_manager/` (config, fetcher, validation, staging, reload, main).

---

## 6. Konventionen für Weiterentwicklung

- **Eine `docker-compose.yaml`** für alle Knoten; keine Compose-Varianten pro Host. Unterschiede nur über `.env`.
- **HAProxy-Config**: Stateless, modulare Dateien in `conf/conf.d/` (nummeriert für Ladereihenfolge); Routing über `maps/routing.map`, Rate-Limits über `maps/rate-limits.map`; Stats-Socket unter `/var/run/haproxy-stat/socket` (geteilt mit Geo-Manager).
- **Python (geo-manager)**:
  - Paketname: `geo_manager` (Unterverzeichnis `geo-manager/geo_manager/`).
  - Einstieg: `python -m geo_manager` (siehe `__main__.py`).
  - Nur Standardbibliothek für Runtime (optional requests/maxminddb später); Tests: pytest, pytest-cov.
- **Tests (geo-manager)**: Immer mit `--cov=geo_manager --cov-fail-under=100` laufen lassen; neue Logik in `geo_manager` durch Tests abdecken.
- **Tests (HAProxy-Integration)**: Bei Änderungen an HAProxy-Config (`conf/conf.d/`, `conf/maps/`), WAF-Config oder Entrypoint: `./scripts/run-haproxy-tests.sh` ausführen. Neue Sicherheitsfeatures dort durch Testfälle absichern. Testdateien unter `tests/haproxy/test_*.py`; Fixtures in `tests/haproxy/conftest.py`.
- **Sprache**: Kommentare und Commit-Messages auf Deutsch oder Englisch konsistent halten; Nutzerdokumentation (README) auf Deutsch.

---

## 7. Typische Aufgaben und wo ansetzen

| Aufgabe | Wo anfangen |
|--------|--------------|
| Neue ENV-Variable | `geo-manager/geo_manager/config.py`, `.env.example`, ggf. `docker-compose.yaml` (environment). |
| Andere Geo-Quelle (z. B. MMDB) | `geo-manager/geo_manager/fetcher.py`; Tests in `geo-manager/tests/test_fetcher.py`. |
| Staged-Delays ändern | `config.py` (Defaults), `.env.example`; Verhalten in `staging.py`. |
| HAProxy-Frontend/Backend anpassen | `conf/conf.d/50-frontend-https.cfg` (Frontend), `conf/conf.d/60-backends.cfg` (Backends); Routing in `conf/maps/routing.map`. |
| Neue Route / neues Rate-Limit | `conf/maps/routing.map`, `conf/maps/rate-limits.map`, ggf. `conf/conf.d/30-stick-tables.cfg`. |
| Coraza/WAF-Regeln | `conf/coraza-spoa.yaml`, `coraza/rules/coreruleset/` (Submodule), Anpassungen in `coraza/rules/custom/`. |
| CI anpassen | `.github/workflows/ci.yml` (Tests, Docker-Builds, HAProxy-Integration). |
| Neuer HAProxy-Integrationstest | `tests/haproxy/test_*.py` (neuer Test), `tests/haproxy/conftest.py` (Fixtures/Helpers), ggf. `tests/haproxy/fixtures/dummy_backend.py` (Backend-Verhalten). |

---

## 8. Tests ausführen

### Geo-Manager Unit-Tests (100 % Coverage)

```bash
cd geo-manager
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=. .venv/bin/pytest tests/ -v --cov=geo_manager --cov-fail-under=100 --cov-report=term-missing
```

### HAProxy-Integrationstests (Docker-basiert)

```bash
./scripts/run-haproxy-tests.sh                     # Alle 38 Tests
./scripts/run-haproxy-tests.sh -k test_waf -v      # Nur WAF-Tests
./scripts/run-haproxy-tests.sh -k test_routing      # Nur Routing-Tests
PYTEST_ARGS="-x -v" ./scripts/run-haproxy-tests.sh # Stopp beim ersten Fehler
```

Das Skript baut die Testumgebung (HAProxy + Coraza + Dummy-Backend + Test-Runner) per Docker Compose, führt pytest aus und räumt danach auf. Voraussetzungen: Docker (Compose v2), openssl. Laufzeit ca. 30–60 s.

Testbereiche: Routing, Per-IP Rate-Limiting, Backend-Überlastungsschutz, Coraza WAF (SQLi/XSS/RCE/Auto-Ban), Geo-Blocking + Map-Reload, SSL-Cert-Reload ohne Downtime, Cluster-Verbindungslimit. Ausführliche Doku: `Dokumentationen/Integrationstests.md`.

### Docker-Build (Smoke-Test)

```bash
docker build -f geo-manager/Dockerfile -t geo-manager:test .
docker build -f coraza/Dockerfile.coraza -t coraza-spoa:test .
```

---

## 9. Referenzen

- **Dokumentationen**: Ordner `Dokumentationen/` – hier liegen alle Projekt-Dokumentationen (Installation, Betrieb, Wartung usw.). Einstieg: `Dokumentationen/README.md`; ausführliche Installationsanleitung: `Dokumentationen/Installation.md`; Integrationstests: `Dokumentationen/Integrationstests.md`.
- **Installation/Historie**: `sys-doku.md` (bestehende Server-Setups, WireGuard, Zertifikate).
- **Spezifikation/Plan**: Cursor-Plan „HA Geo-Blocking Staged Rollout“ (Architektur, Phasen, Implementierungsreihenfolge).
- **Deployment**: `README.md` (Deployment, Geo-Manager-Kurzbeschreibung, Tests).

---

## 10. Hinweise für den Agenten

- Beim Ändern der Safety-Pipeline (Validierung, Staging, Anchor-Check) zuerst Tests anpassen/erweitern und 100 % Coverage beibehalten.
- Keine sensiblen Werte (Passwörter, Tokens) in Repo committen; nur Platzhalter in `.env.example`.
- Bei neuen Abhängigkeiten: `geo-manager/requirements.txt` (Runtime) bzw. `requirements-dev.txt` (Tests) aktualisieren; Dockerfile anpassen, falls nötig.
