# Dokumentationen

In diesem Ordner werden alle Projekt-Dokumentationen abgelegt: Installationsanleitungen, Betrieb, Architektur, Wartung und weitere Themen rund um das HAProxy-/Geo-Manager-Setup.

**Verfügbare Dokumente:**

| Dokument | Inhalt |
|----------|--------|
| [Installation.md](Installation.md) | Vollständige Installationsanleitung: Daten auf den Server bringen, externe Dateien vorbereiten, Start und Überwachung (für kritische Infrastruktur). |
| [Zertifikatserneuerung.md](Zertifikatserneuerung.md) | Let's Encrypt (Certbot + Cloudflare-DNS), SSH-Setup, Deploy-Script für HAProxy-Zertifikate auf alle Knoten, Cron/Deploy-Hook. |
| [Sicherheitsbewertung.md](Sicherheitsbewertung.md) | Production-Readiness, Sicherheitsbewertung und Checkliste vor Produktiveinsatz (Stats, Reload, Geo-Manager, Mesh). |
| [Ausfall-Szenarien.md](Ausfall-Szenarien.md) | Runbook: Was tun bei Geo-Fetch-Fehler, Reload-Fehler, Fail-Open, Master ausgefallen, Rollback der Maps. |
| [Rate-Limiting.md](Rate-Limiting.md) | Per-IP Rate-Limiting auf HAProxy-Ebene (cluster-weit): Limits, Architektur, Konfiguration, Vergleich mit API-Limits. |

Neue Doku-Dateien einfach hier anlegen und in der Tabelle oben ergänzen.
