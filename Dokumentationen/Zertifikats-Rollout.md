# Zertifikats-Rollout im Cluster (cert-manager)

Dieses Dokument beschreibt den neuen **cert-manager-Sidecar**, der TLS-Zertifikate für HAProxy im Cluster verteilt. Ziel ist es, den bisherigen SSH-basierten Rollout (`scripts/deploy-haproxy-certs.sh`) perspektivisch durch einen In-Cluster-Mechanismus zu ergänzen bzw. zu ersetzen.

Der cert-manager ist bewusst einfach gehalten und folgt dem Muster des Geo-Managers:

- **Leader/Follower-Logik über ein Flag `CERT_IS_MASTER` plus `NODE_PRIO`**
- **Staged Rollout** (z. B. 1h/2h Verzögerung für Follower)
- **Nur internes Mesh (WireGuard)**, kein externer SSH-Key-Transfer

---

## 1. Architekturüberblick

- **cert-manager-Container pro Knoten**, zusätzlich zu HAProxy, Coraza-SPOA und Geo-Manager.
- **Leader (Master, `NODE_PRIO=1`)**:
  - Liest die von Certbot erzeugten Dateien (`fullchain.pem`, `privkey.pem`) aus einem read-only Bind-Mount.
  - Baut daraus ein für HAProxy geeignetes `haproxy.pem` (Fullchain + Private Key).
  - Schreibt dieses PEM an den gemeinsamen Zielpfad (z. B. `/etc/ssl/certs/haproxy.pem`), den HAProxy bereits aus dem Host/Volume mountet.
  - Stellt über `/cert/status` den aktuellen Zertifikats-Status bereit.
  - Stellt über `/cert/download` das aktuelle PEM bereit.
- **Follower (Prio 2 und 3, `NODE_PRIO=2/3`)**:
  - Fragen periodisch (`CERT_POLL_INTERVAL_SECONDS`) beim Master `/cert/status` ab.
  - Warten die jeweils konfigurierte Verzögerung (`CERT_STAGE_DELAY_PRIO2_HOURS` / `CERT_STAGE_DELAY_PRIO3_HOURS`) ab, bevor sie ein neues Zertifikat übernehmen.
  - Laden bei Bedarf das PEM über `/cert/download` vom Master und schreiben es lokal nach `/etc/ssl/certs/haproxy.pem`.
- **Kommunikation**:
  - Rein HTTP (kein TLS), aber ausschließlich im bereits gehärteten WireGuard-Mesh.
  - Der Port (`CERT_STATUS_PORT`, Default 8081) wird **nicht** nach außen gemappt, sondern ist nur im Compose-Netz/auf dem Host erreichbar.

Der cert-manager führt **keine eigene ACME/Certbot-Logik** aus. Certbot bleibt auf dem Master-Host zuständig und legt die Zertifikate wie bisher unter `/etc/letsencrypt/live/<DOMAIN>/` ab. Der cert-manager liest diese Dateien nur aus einem Read-Only-Mount.

---

## 2. Umgebungskonfiguration (ENV)

Die relevanten Variablen sind in `.env.example` dokumentiert und müssen pro Knoten
konsequent gesetzt werden:

- **Identität / Cluster (geteilt mit Geo-Manager):**
  - `NODE_NAME` – z. B. `agt-1`, `agt-2`, `agt-3`
  - `NODE_PRIO` – `1` = höchste Priorität, `2`/`3` = Follower (nur für Staged-Delay)
  - `MESH_NODES` – Kommaseparierte Liste der Mesh-IPs aller Knoten (gleiche Reihenfolge wie für Geo-Manager)

- **cert-manager-Rolle:**
  - `CERT_IS_MASTER` – `true`/`false` (oder `1`/`0`): Kennzeichnet genau einen Knoten als Zertifikats-Master.
    - Auf genau **einem** Knoten im Cluster muss `CERT_IS_MASTER=true` gesetzt sein.
    - Wenn mehr als ein Knoten `CERT_IS_MASTER=true` meldet, deaktiviert der cert-manager den Rollout-Mechanismus automatisch (es findet keine Zertifikatsverteilung statt, bis der Konflikt behoben ist).

- **Certbot-Quellen (nur Master nötig):**
  - `CERT_SOURCE_FULLCHAIN` – Pfad im cert-manager-Container zu `fullchain.pem`
  - `CERT_SOURCE_PRIVKEY` – Pfad im cert-manager-Container zu `privkey.pem`

  Beispiel (auf `agt-1`):

  ```env
  CERT_SOURCE_FULLCHAIN=/certs/fullchain.pem
  CERT_SOURCE_PRIVKEY=/certs/privkey.pem
  ```

  In `docker-compose.yaml` muss dazu auf dem Master-Host ein Bind-Mount konfiguriert werden, z. B.:

  ```yaml
  # Beispiel (nur auf dem Master-Server):
  volumes:
    - /etc/letsencrypt/live/example.org/fullchain.pem:/certs/fullchain.pem:ro
    - /etc/letsencrypt/live/example.org/privkey.pem:/certs/privkey.pem:ro
  ```

- **Zielpfad für HAProxy:**
  - `CERT_TARGET_PEM_PATH` – Standard: `/etc/ssl/certs/haproxy.pem`

  Dieser Pfad entspricht dem Pfad, den HAProxy bereits als Bind-Mount aus `./ssl` erhält. Der cert-manager schreibt dort die kombinierte PEM-Datei hin.

- **Statusport und Poll-Intervalle:**
  - `CERT_STATUS_PORT` – HTTP-Port des cert-manager im Container (Default: 8081)
  - `CERT_STAGE_DELAY_PRIO2_HOURS` – Verzögerung für Prio-2-Follower (Default: 1 Stunde)
  - `CERT_STAGE_DELAY_PRIO3_HOURS` – Verzögerung für Prio-3-Follower (Default: 2 Stunden)
  - `CERT_POLL_INTERVAL_SECONDS` – Poll-Intervall der Follower (Default: 300 Sekunden)

- **Cluster-Schlüssel (optional für spätere HMAC/Signaturen):**
  - `CERT_CLUSTER_KEY` – Pre-Shared Key für zusätzliche Integritäts-/Authentizitätsprüfungen der Zertifikats-Rollouts.
  - Muss **auf allen Knoten identisch** gesetzt werden, wird aber **nicht ins Repository** eingecheckt.
  - Der Key wird für alle internen HTTP-Aufrufe `/cert/status` und `/cert/download` zwischen den cert-manager-Knoten als Query-Parameter `cluster_key=<…>` genutzt. Ohne oder mit falschem Key werden diese Aufrufe mit `403 Forbidden` abgelehnt.

### 2.1 Rollen-spezifische .env-Beispiele

- **agt-1 (Master, Zertifikats-Master + Geo-Master):**

  ```env
  NODE_NAME=agt-1
  NODE_PRIO=1
  CERT_IS_MASTER=true
  MESH_NODES=172.20.0.1,172.20.0.2,172.20.0.3

  CERT_LE_DIR_HOST=/etc/letsencrypt/live/example.org
  CERT_SOURCE_FULLCHAIN=/certs/fullchain.pem
  CERT_SOURCE_PRIVKEY=/certs/privkey.pem
  CERT_TARGET_PEM_PATH=/etc/ssl/certs/haproxy.pem

  CERT_CLUSTER_KEY=<starker-pre-shared-key>
  ```

- **agt-2 (Follower, Prio 2):**

  ```env
  NODE_NAME=agt-2
  NODE_PRIO=2
  CERT_IS_MASTER=false
  MESH_NODES=172.20.0.1,172.20.0.2,172.20.0.3

  CERT_LE_DIR_HOST=/etc/letsencrypt/live/example.org   # kann existieren, muss aber keine echten Zertifikate enthalten
  CERT_TARGET_PEM_PATH=/etc/ssl/certs/haproxy.pem

  CERT_CLUSTER_KEY=<starker-pre-shared-key>
  ```

- **agt-3 (Follower, Prio 3):**

  ```env
  NODE_NAME=agt-3
  NODE_PRIO=3
  CERT_IS_MASTER=false
  MESH_NODES=172.20.0.1,172.20.0.2,172.20.0.3

  CERT_LE_DIR_HOST=/etc/letsencrypt/live/example.org   # optional / leer
  CERT_TARGET_PEM_PATH=/etc/ssl/certs/haproxy.pem

  CERT_CLUSTER_KEY=<starker-pre-shared-key>
  ```

---

## 3. Endpunkte des cert-manager

Der cert-manager startet im Container einen einfachen HTTP-Server auf `0.0.0.0:CERT_STATUS_PORT` mit folgenden Endpunkten:

- `GET /health`
  - Rückgabe: `200 OK`, Body `OK`
  - Zweck: Liveness/Health-Check für Compose/Monitoring.

- `GET /cert/status`
  - Rückgabe: JSON mit Feldern:
    - `node_prio` – 1, 2 oder 3
    - `node_name` – z. B. `agt-1`
    - `cert_is_master` – `true`/`false`, ob dieser Knoten der Zertifikats-Master ist
    - `version` – SHA256-Hash des aktuell aktiven PEM (oder `null`, wenn keins aktiv ist)
    - `validated_since` – ISO8601-Zeitstempel, wann dieses PEM zuletzt erfolgreich geschrieben/validiert wurde
  - Follower nutzen diesen Endpunkt, um festzustellen, ob es beim Master eine neue Zertifikatsversion gibt und seit wann diese aktiv ist. Zusätzlich prüfen sie, dass es **genau einen** Master gibt; bei 0 oder >1 Mastern wird der Rollout deaktiviert.

- `GET /cert/download?version=<hash>`
  - Rückgabe:
    - `200 OK` + PEM-Datei (`application/x-pem-file`) bei bekannter Version
    - `404` falls keine passende Version oder kein Zertifikat vorhanden ist
  - Follower laden darüber das PEM vom Master. Nach dem Download wird die Integrität durch Vergleich des lokalen SHA256-Hashes mit `version` geprüft.

---

## 4. Ablauf Leader/Follower

### 4.1 Leader (Master, Prio 1)

1. Beim Start (nur wenn `CERT_IS_MASTER=true`):
   - Liest `CERT_SOURCE_FULLCHAIN` und `CERT_SOURCE_PRIVKEY`.
   - Prüft rudimentär, ob beide Dateien wie erwartete Zertifikat-/Key-Dateien aussehen.
   - Bildet ein kombiniertes PEM (`fullchain + privkey`) und schreibt es atomar nach `CERT_TARGET_PEM_PATH` (über `<path>.new` + `os.replace`).
   - Aktualisiert internen Zustand (`version`, `validated_since`).
2. Weitere Aktualisierungen (z. B. nach `certbot renew`) können über einen später nachgerüsteten Cron/Hooks oder durch Neustart des Containers ausgelöst werden.

### 4.2 Follower (Prio 2 und 3)

1. Pollt in einem Hintergrundthread regelmäßig (`CERT_POLL_INTERVAL_SECONDS`) alle IPs in `MESH_NODES`:
   - Ruft auf jedem Knoten `GET /cert/status` auf.
   - Sammelt alle Knoten, die `cert_is_master=true` melden.
   - Wenn **kein** oder **mehr als ein** Master gefunden wird, wird der Rollout als unsicher betrachtet und für diesen Zyklus abgebrochen (kein Zertifikatswechsel).
2. Wenn der Master eine Version meldet, die lokal noch nicht aktiv ist:
   - Berechnet aus `validated_since` und `CERT_STAGE_DELAY_PRIO*_HOURS`, ob die Verzögerung für diesen Follower bereits abgelaufen ist (`should_activate`).
   - Wenn noch zu früh: keine Aktion, Warten bis zum nächsten Poll.
3. Wenn die Verzögerung abgelaufen ist:
   - Ruft `GET /cert/download?version=<hash>` auf dem Master auf.
   - Prüft, dass der lokale SHA256-Hash des Downloads mit `<hash>` übereinstimmt.
   - Schreibt das geladene PEM atomar nach `CERT_TARGET_PEM_PATH` und aktualisiert den lokalen Zustand.

---

## 5. Integration in docker-compose

In `docker-compose.yaml` ist der cert-manager als eigener Dienst eingetragen:

- Nutzung desselben Netzwerks `security-net`.
- Gemeinsames Volume `./ssl:/etc/ssl/certs` mit HAProxy, sodass beide auf dieselbe PEM-Datei zugreifen.
- Environment-Variablen wie oben beschrieben (`NODE_NAME`, `NODE_PRIO`, `MESH_NODES`, `CERT_*`).
- Kein Port-Mapping nach außen – der HTTP-Dienst ist nur im Compose-Netz bzw. über den Host erreichbar.

Auf dem Master-Host muss zusätzlich ein Bind-Mount zu den Certbot-Dateien konfiguriert werden (siehe Beispiel in Abschnitt 2), damit cert-manager auf `fullchain.pem` und `privkey.pem` zugreifen kann.

---

## 6. Sicherheitshinweise

- **Private Keys verlassen nie den Master-Host unverschlüsselt außerhalb des Meshes.**
  - Die Verteilung erfolgt nur zwischen cert-manager-Containern innerhalb des abgesicherten WireGuard-Mesh.
- **`CERT_CLUSTER_KEY`**:
  - Ist als Vorbereitung für signierte bzw. HMAC-gesicherte Zertifikats-Rollouts vorgesehen.
  - Aktueller Stand: Platzhalter, noch keine Signatur-Logik im Code – es werden lediglich Hashes (`version`) geprüft.
  - Der Key darf **nicht** ins Repository eingecheckt werden und sollte pro Cluster eindeutig sein.
- **Ports:**
  - `CERT_STATUS_PORT` (Default 8081) **nicht** nach außen mappen, sondern nur im internen Netzwerk nutzen.
  - Firewall-Regeln so setzen, dass ausschließlich das Mesh / die HAProxy-Knoten Zugriff haben.
- **Fehlerhandling:**
  - Wenn der Master kein gültiges PEM erzeugen kann (fehlende oder leere Dateien, Formatfehler), wird kein neuer Zustand gesetzt.
  - Follower übernehmen nur Zertifikate, die sie erfolgreich vom Master geladen und per Hash geprüft haben.

---

## 7. Betrieb und Tests

- **Lokaler Test des cert-manager (ohne echtes Certbot):**
  - Im Projektverzeichnis:

  ```bash
  cd cert-manager
  python -m venv .venv
  . .venv/bin/activate
  pip install -r requirements.txt
  pytest -v
  ```

  - Die Tests prüfen u. a.:
    - Laden und Zusammenführen von Fullchain/Privkey.
    - Atomisches Schreiben der PEM-Datei.
    - Berechnung und Verwendung der Zertifikats-Version (`SHA256`).
    - Staged-Rollout-Entscheidungslogik (`should_activate`).

- **Integrationstest im Cluster:**
  1. Auf dem Master-Host Certbot wie gehabt konfigurieren (`/etc/letsencrypt/...`).
  2. Bind-Mounts für `fullchain.pem` und `privkey.pem` in `docker-compose.yaml` ergänzen.
  3. `.env` auf allen Knoten um die `CERT_*`-Variablen ergänzen.
  4. `docker compose up -d --build` ausführen.
  5. Auf dem Master:
     - `curl http://localhost:8081/health`
     - `curl http://localhost:8081/cert/status`
  6. Auf Followern:
     - Nach Ablauf der Verzögerung per `curl http://localhost:8081/cert/status` prüfen, ob `version` der des Masters entspricht.

Der bisherige SSH-basierte Rollout (`scripts/deploy-haproxy-certs.sh`) kann parallel weiter genutzt werden, bis der cert-manager im Betrieb ausreichend getestet und freigegeben ist.

