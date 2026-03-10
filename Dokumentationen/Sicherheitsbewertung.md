# Sicherheitsbewertung und Production-Readiness

**Kontext:** BOS-Anwendung (lebens-/einsatzkritisch). Bewertung fokussiert auf diese Anwendung (HAProxy, Geo-Manager, Coraza); externe APIs sind separat rate-limited und validiert.

---

## 1. Kurzfassung

| Aspekt | Bewertung | Hinweis |
|--------|-----------|---------|
| **Production Ready** | **Ja, unter Auflagen** | Kritische Punkte (Stats-Auth, Bindung, Reload) sind adressiert; Betrieb setzt saubere Netz-/Firewall-Regeln und korrektes ENV-Setup voraus. |
| **Sicherheitsrisiken** | **Mittel** | Restrisiken v. a. bei Mesh/Ports (8080) und file://-Konfiguration; mit den beschriebenen Betriebsauflagen vertretbar. |
| **Logik / Ausfall** | **Beherrschbar** | Safety-Pipeline (Validierung, Staged Rollout) ist stimmig; Reload läuft nun ohne Shell-Injection-Risiko. |

---

## 2. Kritische Befunde

### 2.1 HAProxy Stats: Zugangsdaten im Repository und Bindung

- **Ort:** `conf/haproxy.cfg` Zeilen 44–50.
- **Problem:**
  - `stats auth admin:RaLL8ATBg274qpTs` ist **fest im Repo** hinterlegt. Jeder mit Repo-Zugriff kennt das Passwort.
  - `stats admin if TRUE` erlaubt mit diesem Passwort **volle Admin-Funktionen** (Reload, Server deaktivieren, Konfiguration auslesen usw.).
  - Stats-Frontend ist mit `bind :56708` auf **allen Interfaces** gebunden; in `docker-compose.yaml` ist Port **56708** nach außen gemappt.
- **Risiko:** Wenn Port 56708 aus dem Netz erreichbar ist (z. B. Firewall öffnet ihn), kann ein Angreifer mit dem bekannten Passwort die gesamte HAProxy-Instanz steuern und den Dienst lahmlegen oder umkonfigurieren.
- **Empfehlung:**
  1. **Keine echten Zugangsdaten im Repo.** Stats-Auth per Deployment (z. B. envsubst beim Start, oder separate Config pro Umgebung) setzen und nur Platzhalter/Beispiel in Repo lassen.
  2. Stats-Frontend nur binden, wo nötig: z. B. `bind 127.0.0.1:56708` und Port 56708 in `docker-compose` **nicht** veröffentlichen, Zugriff nur über Host (z. B. `docker exec` oder SSH-Port-Forward).
  3. In der Doku (z. B. Installation/Checkliste) festhalten: Stats-Passwort und -URI pro Umgebung setzen und Port-Freigabe prüfen.

---

### 2.2 Reload: Shell-Interpolation (Command Injection)

- **Ort:** `geo-manager/geo_manager/reload.py`, Zeile 27.
- **Problem:**  
  `socket_path` wird in einen Shell-String eingebettet:  
  `subprocess.run(["sh", "-c", f'echo "reload" | socat stdio "{socket_path}"'])`  
  Wenn `HAPROXY_SOCKET` jemals aus einer unsicheren Quelle käme (z. B. fehlerhafte Konfiguration, später erweiterte Konfig-Source), könnten darin enthaltene Shell-Metazeichen zu beliebiger Befehlsausführung führen.
- **Risiko:** Aktuell wird der Wert aus der Umgebung (Betreiber) gesetzt → mittleres Risiko. Für eine kritische Anwendung ist das Muster trotzdem inakzeptabel.
- **Empfehlung:** Reload **ohne Shell** ausführen, z. B. mit fester Argumentliste:  
  `subprocess.run(["socat", "STDIO", f"UNIX-CONNECT:{socket_path}"], input="reload\n", ...)`  
  So wird `socket_path` nur als einzelner Argument-String an socat übergeben, keine Shell-Interpretation.

---

## 3. Hohe / Mittlere Befunde

### 3.1 Geo-Manager Status-Port 8080 ohne Authentifizierung

- **Ort:** `geo-manager/geo_manager/main.py` – HTTP-Server auf `0.0.0.0:8080`; Endpunkte `/health`, `/metrics`, `/cluster`, `/geo/status` ohne Auth.
- **Problem:** Wer den Dienst auf Port 8080 erreicht, kann Cluster-Topologie, Knotennamen, `validated_at` und Rollout-Status auslesen.
- **Risiko:** Informationsoffenlegung und bessere Angriffsplanung; bei Erreichbarkeit von außen (z. B. 8080 am Host gemappt und nicht gefiltert) zusätzlich Angriffsfläche für DoS (z. B. viele Anfragen an den single-threaded Handler).
- **Empfehlung:**  
  - Port 8080 in Produktion nur im Mesh (z. B. WireGuard) oder auf localhost erreichbar machen (Firewall/Netzplanung).  
  - Optional: Bindung auf `127.0.0.1` im Container und Zugriff nur über lokales Port-Forwarding; oder Auth (z. B. API-Key/Token) für `/geo/status`, `/cluster`, `/metrics` einführen.

---

### 3.2 file://-URLs im Geo-Fetcher

- **Ort:** `geo-manager/geo_manager/fetcher.py`, `download_url()` – bei `file://` wird `parsed.path` gelesen und geöffnet.
- **Problem:** Mit `GEO_SOURCE_URL=file:///etc/passwd` (oder anderer Pfad) könnte theoretisch jede lesbare Datei gelesen werden. Die URL kommt aktuell aus der Umgebung (Betreiber).
- **Risiko:** Mittleres Risiko (Misconfiguration, z. B. wenn ENV irgendwann aus einer weniger vertrauenswürdigen Quelle gesetzt wird).
- **Empfehlung:** Für `file://` nur Pfade unter einem erlaubten Präfix (z. B. `/data` oder `MAP_DIR`) zulassen und bei Verletzung abbrechen.

---

### 3.3 Vertrauen in Mesh-Knoten (Staged Rollout)

- **Ort:** `geo-manager/geo_manager/staging.py` – Follower holen `/geo/status` von `MESH_NODES` und vertrauen dem ersten Knoten mit `node_prio=1`.
- **Problem:** Ein kompromittierter oder gefälschter Knoten im Mesh könnte sich als Master ausgeben und z. B. `validated_at` in der Vergangenheit liefern, sodass Follower vorzeitig aktivieren.
- **Risiko:** Abhängig von der Absicherung des Meshes (z. B. WireGuard, Zugriffskontrolle). Ohne starkes Mesh-Vertrauen: mittleres Risiko.
- **Empfehlung:** Mesh (WireGuard, Zugriff nur von bekannten Knoten) strikt absichern; optional gegenseitige Authentisierung/TLS für Status-Abfragen prüfen.

---

### 3.4 Sehr große Geo-Daten (DoS / Ressourcen)

- **Ort:** Fetcher lädt die komplette Antwort in den Speicher; Validierung (Size, Anchors) und Map-Bau arbeiten auf dem vollen String.
- **Problem:** Eine extrem große oder gezielt aufgeblähte Geo-Quelle könnte hohen Speicherverbrauch oder OOM im Container verursachen.
- **Risiko:** Gering bis mittel, wenn die Geo-URL unter Kontrolle des Betreibers ist; höher, wenn die URL jemals aus unsicherer Quelle käme.
- **Empfehlung:** Maximalgröße für Download (z. B. Content-Length-Check oder Limit beim Lesen) und/oder Timeout; ggf. Streaming-Verarbeitung, wo möglich.

---

## 4. Logik und Ausfallsicherheit

- **Ablauf Master/Follower:** Nur Prio-1 lädt und aktiviert; Follower warten 48h/96h und prüfen `validated_at` des Masters – konsistent mit der Spezifikation.
- **Validierung vor Aktivierung:** Size-Check, Anchor-Check und HAProxy-Syntax-Check laufen vor dem endgültigen Aktivieren; bei Syntaxfehler wird Backup wiederhergestellt – sinnvoll.
- **Keine Schreib-API:** Der Geo-Manager bietet nur GET-Endpunkte; es gibt keine API, über die externe Aufrufer Maps oder Konfiguration ändern könnten – gut.
- **Reload:** Erfolgt erst nach erfolgreicher Validierung; bei Reload-Fehler wird eine Exception geworfen, Aktivierung wird nicht als erfolgreich markiert – korrekt.

---

## 5. Production-Readiness: Weitere Punkte

| Thema | Status | Empfehlung |
|-------|--------|------------|
| Healthcheck Geo-Manager | Fehlt in `docker-compose` | `healthcheck` für den Geo-Manager-Container ergänzen (z. B. GET /health auf 8080). |
| Stats-Passwort/URI | Im Repo und unsicher | Siehe 2.1 – aus Repo entfernen, über Deployment/ENV setzen. |
| Logging | stdout, Level konfigurierbar | Für Produktion strukturiertes Logging (z. B. JSON) und Log-Level aus ENV erwägen. |
| Ressourcenlimits | Gesetzt (CPU/RAM) | Bereits in docker-compose definiert – beibehalten. |
| Secrets (Mail, etc.) | Über ENV, nicht im Repo | Korrekt; .env nicht committen. |

---

## 6. Checkliste vor Produktiveinsatz

- [ ] **Stats:** Kein echtes Passwort/keine echte URI im Repo; Stats-Auth und ggf. URI pro Umgebung setzen (envsubst oder separate Config).
- [ ] **Stats-Port:** Nur binden wo nötig (z. B. 127.0.0.1); Port 56708 nicht nach außen mappen oder Firewall so, dass nur Vertrauensbereiche zugreifen.
- [ ] **Reload:** Aufruf ohne Shell (kein `sh -c` mit Interpolation); siehe Empfehlung 2.2.
- [ ] **Geo-Manager 8080:** Netzwerk/Firewall so, dass 8080 nur aus dem Mesh oder localhost erreichbar ist (oder Auth für sensible Endpunkte).
- [ ] **file://:** Nur erlaubte Verzeichnisse für file://-URLs zulassen.
- [ ] **Healthcheck** für Geo-Manager in docker-compose ergänzen.
- [ ] **Dokumentation:** In Installation/Checkliste Sicherheitshinweise zu Stats, Mesh und Port-Freigaben aufnehmen.

---

*Stand: Bewertung der Codebasis; regelmäßige Überprüfung bei Änderungen empfohlen.*
