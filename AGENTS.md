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
| `conf/` | HAProxy- und WAF-Konfiguration: `haproxy.cfg`, `coraza.cfg`, `coraza-spoa.yaml`, `errors/`, `maps/` (Start-Maps), `promtail-config.yaml`. |
| `conf/maps/` | Initiale `geo.map` und `whitelist.map`; Geo-Manager überschreibt sie. |
| `ssl/` | `haproxy.pem` (Fullchain+Privkey), pro Server befüllen, nicht committen. |
| `coraza/` | `Dockerfile.coraza` (Coraza SPOA Build), `rules/` (OWASP CRS, z. B. via `git clone` coreruleset). |
| `geo-manager/` | Python-Paket `geo_manager`: Config, Fetcher, Validierung, Staging, Reload, HTTP-Status; plus Tests. |
| `geo-manager/geo_manager/` | Quellcode: `config.py`, `fetcher.py`, `validation.py`, `staging.py`, `reload.py`, `main.py`, `__main__.py`. |
| `geo-manager/tests/` | Pytest-Tests; Ziel 100 % Coverage für `geo_manager`. |
| `.env.example` | Vorlage für alle ENV-Variablen; pro Server eigene `.env`. |
| `docker-compose.yaml` | Einheitliche Definition für haproxy, coraza-spoa, geo-manager. |
| `Dokumentationen/` | Projekt-Dokumentationen: Installation, Betrieb, Architektur, Wartung. Hier werden alle Doku-Dateien abgelegt (siehe `Dokumentationen/README.md`). |
| `scripts/` | Hilfsskripte: `gen-dev-cert.sh` (lokaler Test: SSL + Socket), `deploy-haproxy-certs.sh` (Let's Encrypt-Zertifikate auf alle Knoten deployen). Konfiguration Deploy: `scripts/cert-deploy.env` (Vorlage: `cert-deploy.env.example`). |

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
- **HAProxy-Config**: Stateless, eine gemeinsame `conf/haproxy.cfg`; Geo/Whitelist-ACLs und Map-Pfade wie in Spezifikation; Stats-Socket unter `/var/run/haproxy-stat/socket` (geteilt mit Geo-Manager).
- **Python (geo-manager)**:
  - Paketname: `geo_manager` (Unterverzeichnis `geo-manager/geo_manager/`).
  - Einstieg: `python -m geo_manager` (siehe `__main__.py`).
  - Nur Standardbibliothek für Runtime (optional requests/maxminddb später); Tests: pytest, pytest-cov.
- **Tests**: Immer mit `--cov=geo_manager --cov-fail-under=100` laufen lassen; neue Logik in `geo_manager` durch Tests abdecken.
- **Sprache**: Kommentare und Commit-Messages auf Deutsch oder Englisch konsistent halten; Nutzerdokumentation (README) auf Deutsch.

---

## 7. Typische Aufgaben und wo ansetzen

| Aufgabe | Wo anfangen |
|--------|--------------|
| Neue ENV-Variable | `geo-manager/geo_manager/config.py`, `.env.example`, ggf. `docker-compose.yaml` (environment). |
| Andere Geo-Quelle (z. B. MMDB) | `geo-manager/geo_manager/fetcher.py`; Tests in `geo-manager/tests/test_fetcher.py`. |
| Staged-Delays ändern | `config.py` (Defaults), `.env.example`; Verhalten in `staging.py`. |
| HAProxy-Frontend/Backend anpassen | `conf/haproxy.cfg`; Geo/Whitelist-Reihenfolge beibehalten (vor WAF). |
| Coraza/WAF-Regeln | `conf/coraza-spoa.yaml`, `coraza/rules/`. |
| CI anpassen | `.github/workflows/ci.yml` (Tests, Docker-Builds). |

---

## 8. Tests ausführen

```bash
cd geo-manager
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=. .venv/bin/pytest tests/ -v --cov=geo_manager --cov-fail-under=100 --cov-report=term-missing
```

Docker-Build (aus Repo-Root):

```bash
docker build -f geo-manager/Dockerfile -t geo-manager:test .
docker build -f coraza/Dockerfile.coraza -t coraza-spoa:test .
```

---

## 9. Referenzen

- **Dokumentationen**: Ordner `Dokumentationen/` – hier liegen alle Projekt-Dokumentationen (Installation, Betrieb, Wartung usw.). Einstieg: `Dokumentationen/README.md`; ausführliche Installationsanleitung: `Dokumentationen/Installation.md`.
- **Installation/Historie**: `sys-doku.md` (bestehende Server-Setups, WireGuard, Zertifikate).
- **Spezifikation/Plan**: Cursor-Plan „HA Geo-Blocking Staged Rollout“ (Architektur, Phasen, Implementierungsreihenfolge).
- **Deployment**: `README.md` (Deployment, Geo-Manager-Kurzbeschreibung, Tests).

---

## 10. Hinweise für den Agenten

- Beim Ändern der Safety-Pipeline (Validierung, Staging, Anchor-Check) zuerst Tests anpassen/erweitern und 100 % Coverage beibehalten.
- Keine sensiblen Werte (Passwörter, Tokens) in Repo committen; nur Platzhalter in `.env.example`.
- Bei neuen Abhängigkeiten: `geo-manager/requirements.txt` (Runtime) bzw. `requirements-dev.txt` (Tests) aktualisieren; Dockerfile anpassen, falls nötig.
