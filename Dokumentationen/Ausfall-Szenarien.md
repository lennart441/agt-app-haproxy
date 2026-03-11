# Ausfall-Szenarien und Runbook (Geo-Manager / HAProxy-Cluster)

Kurze Handlungsanleitungen bei typischen Störungen des Geo-Managers und des HAProxy-Clusters. Das System ist lebens-/einsatzkritisch (BOS); Fehler schnell eingrenzen und beheben.

---

## 1. Geo-Fetch-Fehler (Datenquelle nicht erreichbar / ungültig)

**Symptom:** E-Mail „[Geo-Manager] Fehlschlag nach N Versuchen“, Logs: Fetch-Fehler nach allen Retries, Metrik `geo_fetch_total{outcome="failure"}` steigt.

**Ursachen:** `GEO_SOURCE_URL` (oder `GEO_BLOCKS_URL`/`GEO_LOCATIONS_URL`) nicht erreichbar, Timeout, 4xx/5xx, oder ungültiges Format.

**Maßnahmen:**

1. Erreichbarkeit prüfen: Vom Master-Knoten (Prio 1) `curl -I "$GEO_SOURCE_URL"` (bzw. die konfigurierten URLs).
2. DNS/Netzwerk: Kann der Container die Quelle auflösen und erreichen? Ggf. `docker exec geo-manager wget -O- …` testen.
3. Quelle wechseln: Falls die bisherige URL dauerhaft ausfällt, in `.env` eine Ersatz-URL setzen und Container neu starten (`docker compose up -d geo-manager`).
4. Bis zur Behebung: Der Knoten behält die zuletzt gültigen Maps; Fail-Open tritt nur ein, wenn die neue Liste leer oder zu klein ist.

---

## 2. Validierungsfehler (Size / Anchor / Syntax)

**Symptom:** E-Mail „[Geo-Manager] Validierung fehlgeschlagen (size|anchor|syntax)“, Logs mit entsprechender Meldung, Metrik `geo_validation_failures_total` steigt.

**Size:** Neue Geo-Liste ist deutlich kleiner als die vorherige (unter `SIZE_DEVIATION_THRESHOLD`, z. B. 90 %).  
**Anchor:** Mindestens eine IP aus `ANCHOR_IPS` ist in der neuen Liste nicht als erlaubtes Land (DE/EU) eingetragen.  
**Syntax:** `haproxy -c -f …` schlägt fehl (Config/Map-Syntax).

**Maßnahmen:**

1. **Size:** Quelle prüfen – wurde die Liste gekürzt oder ist der Download unvollständig? Bei legitimer Verkleinerung `SIZE_DEVIATION_THRESHOLD` anpassen oder temporär senken (nur mit Vorsicht).
2. **Anchor:** `ANCHOR_IPS` prüfen; sicherstellen, dass diese IPs in der verwendeten Geo-Datenquelle dem erwarteten Land zugeordnet sind. Ggf. Anker-IPs anpassen.
3. **Syntax:** Auf dem Knoten `haproxy -c -f /pfad/zur/haproxy.cfg` ausführen; Fehlerquelle (Map-Format, Sonderzeichen) beheben. Nach Fix erneut Fetch auslösen (z. B. über Dashboard „Deploy Geo-Listen jetzt“ oder nächster Zyklus).

---

## 3. HAProxy-Reload fehlgeschlagen

**Symptom:** E-Mail „[Geo-Manager] HAProxy-Reload fehlgeschlagen“, Logs: „HAProxy reload failed“ bzw. „Success=0“, Metrik `geo_reload_failure_total` steigt.

**Ursachen:** Master-CLI-Socket nicht erreichbar, HAProxy lehnt die neue Config ab (Syntax/Fehler), Timeout.

**Maßnahmen:**

1. Socket prüfen: Auf dem Knoten `ls -la /var/run/haproxy-stat/` (bzw. konfigurierter Socket); Rechte für den Geo-Manager-Container (gleiches Volume).
2. Manueller Reload-Test: `echo "reload" | socat STDIO UNIX-CONNECT:/var/run/haproxy-stat/master` (von dem Host/Container, von dem aus der Geo-Manager den Socket nutzt). Bei „Success=0“: HAProxy-Logs und Config prüfen.
3. HAProxy-Config validieren: `haproxy -c -f /tmp/haproxy.cfg` (mit den gleichen Maps, die der Geo-Manager geschrieben hat). Bei Fehlern: Map-Dateien und `conf/haproxy.cfg` prüfen.
4. Nach Behebung: „Deploy Geo-Listen jetzt“ erneut auslösen oder warten auf nächsten Fetch-Zyklus.

---

## 4. Fail-Open ausgelöst (Geo-Liste leer oder zu klein)

**Symptom:** E-Mail „[Geo-Manager] Fail-Open: Geo-Liste unbrauchbar“, Logs: „Fail-open: Geo-Liste fehlt (leer)“ oder „… hat nur N Einträge“. **Sicherheitsrelevant:** Alle Zugriffe werden erlaubt.

**Maßnahmen (sofort):**

1. Ursache beheben: Fetch-Quelle und -Erreichbarkeit prüfen (siehe Abschnitt 1). Sicherstellen, dass wieder eine gültige, ausreichend große Liste geladen wird.
2. Optional temporär: `GEO_FAIL_OPEN_MIN_ENTRIES` anheben, um erneutes Fail-Open bei sehr kleinen Listen zu vermeiden – nur Übergang, bis die Quelle wieder liefert.
3. Nach erfolgreichem erneuten Fetch und Validierung aktiviert der Geo-Manager wieder die echten Maps; Fail-Open endet mit dem nächsten erfolgreichen Durchlauf.

---

## 5. Master-Knoten (Prio 1) ausgefallen

**Symptom:** Follower (Prio 2/3) erhalten keine neuen Geo-Daten mehr; Cluster-Dashboard zeigt Master nicht erreichbar; Follower laufen mit letzter übernommener Map weiter.

**Maßnahmen:**

1. Master-Knoten wiederherstellen (Container, Host, Netzwerk). Solange der Master down ist, gibt es keine neuen Geo-Updates für das Cluster.
2. **Kein automatischer Master-Wechsel:** Die Rolle ist über `NODE_PRIO` fest zugewiesen. Bei geplanter Master-Abschaltung: Auf dem neuen Master `NODE_PRIO=1` setzen und auf den alten `NODE_PRIO>1`; alle Knoten neu starten und ggf. Staged-Delays beachten.
3. Bis dahin: Follower bleiben mit der zuletzt übernommenen Map betriebsbereit; nur keine Aktualisierung der Geo-Daten.

---

## 6. Rollback der Geo-Maps (Notfall)

**Szenario:** Eine fehlerhafte oder unerwünschte Map wurde aktiviert und soll zurückgesetzt werden.

**Manueller Rollback (pro Knoten):**

1. Auf dem Knoten: Backup der Maps prüfen. Der Geo-Manager legt vor dem Überschreiben `geo.map.bak` an; nach erfolgreichem Reload wird sie gelöscht. Bei laufendem Betrieb existiert sie nur kurz.
2. **Ohne Backup:** Alte Map-Datei von einem anderen Knoten oder aus einem Backup wiederherstellen nach `conf/maps/geo.map` (bzw. konfigurierter `MAP_DIR`). Dann HAProxy-Reload: `echo "reload" | socat STDIO UNIX-CONNECT:/var/run/haproxy-stat/master`.
3. **Dauerhafte Lösung:** Geo-Quelle korrigieren oder auf eine bekannte gute Version wechseln; auf dem Master „Deploy Geo-Listen jetzt“ ausführen. Follower übernehmen nach Staged-Delay (48h/96h), sofern nicht manuell auf allen Knoten zurückgesetzt wird.

---

## 7. Nützliche Befehle und Stellen

| Ziel | Befehl / Ort |
|------|----------------|
| Geo-Manager Health | `curl -s http://<knoten>:8080/health` |
| Geo-Status (Rolle, validated_at) | `curl -s http://<knoten>:8080/geo/status` |
| Cluster-Übersicht (alle Knoten) | Dashboard `GET /dashboard` (über HAProxy oder direkt cert-manager) |
| Manueller Geo-Deploy (nur Master) | Dashboard „Deploy Geo-Listen jetzt“ oder `curl -X POST http://<master>:8080/geo/deploy-now` |
| HAProxy-Config prüfen | `docker exec haproxy_gateway haproxy -c -f /tmp/haproxy.cfg` |
| Geo-Manager-Logs | `docker logs geo-manager` |

---

Weitere Hinweise: [Installation.md](Installation.md), [Sicherheitsbewertung.md](Sicherheitsbewertung.md). Alert-Beispiele für Prometheus: `monitoring/alerts.yml.example`.
