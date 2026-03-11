# Monitoring (Prometheus + Grafana)

Beispiel-Konfigurationen für die Überwachung von Geo-Manager und HAProxy. Nicht im Container-Stack enthalten; Prometheus und Grafana werden separat betrieben.

## Dateien

| Datei | Beschreibung |
|-------|--------------|
| `prometheus.yml.example` | Scrape-Config für Geo-Manager (Port 8080) und HAProxy (Port 8404). Targets anpassen (Mesh-IPs der drei Knoten). |
| `grafana-dashboard-geo.json` | Vordefiniertes Grafana-Dashboard: Rolle, Last Validated, Fetch/Reload/Fail-Open, Cluster-Erreichbarkeit und Latenz. Nach Import in Grafana ggf. Datasource-UID anpassen (Standard: `prometheus`). |
| `alerts.yml.example` | Beispiel-Alert-Regeln (GeoManagerDown, GeoFailOpen, GeoReloadFailure, GeoFetchFailure, GeoClusterNodeUnreachable). In Prometheus unter rule_files einbinden; Alertmanager-Routing separat konfigurieren. |

## Verwendung

1. **Prometheus:** `prometheus.yml.example` kopieren bzw. den Inhalt in die zentrale Prometheus-Konfiguration übernehmen. Sicherstellen, dass Prometheus die Mesh-IPs und Ports 8080 (Geo-Manager) und 8404 (HAProxy) erreichen kann.
2. **Grafana:** Dashboard-Import → Upload von `grafana-dashboard-geo.json`. Falls die Prometheus-Datenquelle eine andere UID hat, im Dashboard die Datasource auf die vorhandene Prometheus-Instanz umstellen.

## Metriken (Geo-Manager)

- `geo_node_prio`, `geo_is_master` – Rolle des Knotens
- `geo_last_validated_timestamp_seconds` – Zeitstempel der letzten erfolgreichen Map-Aktivierung
- `geo_fetch_total{outcome="success|failure|fail_open"}` – Fetches nach Ausgang
- `geo_validation_failures_total{reason="size|anchor|syntax"}` – Validierungsfehler
- `geo_reload_success_total`, `geo_reload_failure_total` – HAProxy-Reloads
- `geo_fail_open_events_total` – Fail-Open-Ereignisse
- `geo_cluster_node_reachable{node_ip="..."}`, `geo_cluster_node_latency_ms{node_ip="..."}` – Cluster-Health (nach erstem Probe)
