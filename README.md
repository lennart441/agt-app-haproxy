# HAProxy + Geo-Blocking + Staged Rollout (BOS)

Einheitliches Docker-Setup für HAProxy 3+ mit Coraza WAF und Geo-Manager (Safety Pipeline, Staged Rollout). Ein Repo, eine `docker-compose.yaml`; die Differenzierung pro Server erfolgt nur über die `.env`.

## Komponenten

- **HAProxy 3.2**: Loadbalancer, TLS, Geo-IP-ACLs (Maps), SPOE für WAF
- **Coraza SPOA**: WAF-Agent (OWASP CRS)
- **Geo-Manager**: Sidecar für Geo-IP-Maps: Download, Validierung (Syntax, Größe, Anchor-Check), Staged Rollout (Prio 1 sofort, Prio 2 nach 48h, Prio 3 nach 96h)

## Voraussetzungen

- Docker & Docker Compose
- Pro Server: eigene `.env` mit `NODE_NAME`, `NODE_PRIO`, `MESH_NODES`, `ANCHOR_IPS`, `GEO_SOURCE_URL`
- SSL: `ssl/haproxy.pem` (Fullchain + Privkey) pro Server
- Coraza-Regeln: `coraza/rules/` (z. B. `git clone https://github.com/coreruleset/coreruleset.git coraza/rules`)

## Deployment

1. Repo klonen, in das Verzeichnis wechseln.
2. `.env` anlegen (z. B. aus `.env.example` kopieren) und pro Server anpassen:
   - **AGT-1**: `NODE_NAME=agt-1`, `NODE_PRIO=1`
   - **AGT-2**: `NODE_NAME=agt-2`, `NODE_PRIO=2`
   - **AGT-3**: `NODE_NAME=agt-3`, `NODE_PRIO=3`
   - `MESH_NODES` = WireGuard-IPs aller drei (z. B. `172.20.0.1,172.20.0.2,172.20.0.3`)
   - `ANCHOR_IPS` = Komma-getrennte IPs, die in der Geo-Liste als DE/EU gelten müssen (Plausibilitäts-Check).
   - `GEO_SOURCE_URL` = URL zur Geo-IP-CSV (oder `GEO_BLOCKS_URL` + `GEO_LOCATIONS_URL` für MaxMind-Style).
3. `ssl/haproxy.pem` bereitstellen.
4. Coraza-Regeln: `coraza/rules/` befüllen (siehe oben).
5. Start: `docker compose up -d`.

Auf jedem der drei Server denselben Ablauf mit jeweils passender `.env` ausführen. Es wird nur eine `docker-compose.yaml` verwendet.

## Geo-Manager

- **Master (Prio 1)**: Lädt periodisch die Geo-Quelle, prüft Syntax (haproxy -c), Größe und Anchor-IPs, schreibt Maps und löst Reload aus. Setzt danach `validated_at`.
- **Follower (Prio 2/3)**: Fragen den Master per HTTP (`/geo/status`) ab und übernehmen eine neue Map erst, wenn sie beim Master seit 48h (Prio 2) bzw. 96h (Prio 3) fehlerfrei aktiv ist.

Status-Endpunkt: `http://<host>:8080/geo/status` (JSON: `node_prio`, `validated_at`, …).

## Tests

```bash
cd geo-manager
pip install -r requirements-dev.txt  # oder: pytest pytest-cov
pytest tests/ -v --cov=geo_manager --cov-fail-under=100 --cov-report=term-missing
```

## Lizenz / Hinweis

BOS-relevant; Konfiguration und Anpassungen gemäß interner Vorgaben vornehmen.
