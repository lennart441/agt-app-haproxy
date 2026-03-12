# Eigene WAF-Regeln (Custom Rules)

**Alle Anpassungen gehören hierher** – nicht in die Dateien des CRS-Submodules (`coraza/rules/coreruleset/`).

- Regeln in `custom/*.conf` werden **nach** dem OWASP CRS geladen und können CRS-Regeln überschreiben oder ergänzen.
- So bleibt das CRS-Submodule unverändert und kann jederzeit auf eine neue Version aktualisiert werden, ohne dass eure Anpassungen verloren gehen.

Beispiele:
- Bestimmte CRS-Regeln für eure Anwendung deaktivieren (z. B. False Positives)
- Zusätzliche app-spezifische Regeln
- Paranoia-Level oder andere CRS-Optionen anpassen (in `crs-setup.conf.example` im Submodule steht die Referenz; Overrides hier definieren)

**Aktive Anpassung (00-custom.conf):** CRS-Regel 911100 (Method Enforcement) wird durch eine projektweite Ausnahme ersetzt: DELETE, PUT und PATCH sind für die REST-APIs erlaubt; andere Methoden bleiben blockiert.
