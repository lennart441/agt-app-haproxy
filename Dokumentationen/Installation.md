# Installationsanleitung – HAProxy + Geo-Manager (kritische Infrastruktur)

Diese Anleitung beschreibt die Installation des einheitlichen Docker-Setups (HAProxy, Coraza WAF, Geo-Manager) auf einem Serverknoten. Das System ist **lebens- und einsatzkritisch** (BOS/Atemschutzüberwachung). Jeder Schritt ist bewusst ausführlich beschrieben; bei Unsicherheit lieber eine Prüfung mehr durchführen als eine weglassen.

---

## 1. Hinweis zur Kritikalität

- **Keine Experimente auf Produktionsservern.** Änderungen zuerst in einer Testumgebung oder auf einem einzelnen Knoten prüfen.
- **Staged Rollout:** Es gibt drei Knoten; der Geo-Manager aktiviert neue Geo-Maps auf Followern erst nach 48h (Prio 2) bzw. 96h (Prio 3) fehlerfreier Laufzeit des Masters. Das schützt davor, dass fehlerhafte Listen alle Knoten gleichzeitig betreffen.
- **Backup:** Vor größeren Änderungen Konfiguration und wichtige Dateien sichern (z. B. `.env`, `ssl/`, `conf/`).

---

## 2. Voraussetzungen auf dem Server

- **Docker** und **Docker Compose** (v2) installiert und lauffähig.
- **Netzwerk:** Das WireGuard-Mesh zwischen den drei HAProxy-Knoten muss bereits eingerichtet sein; die Mesh-IPs (z. B. 172.20.0.1, 172.20.0.2, 172.20.0.3) müssen auf allen Knoten erreichbar sein.
- **Zugang:** SSH-Zugang und ausreichende Rechte, um Dateien ins Zielverzeichnis zu kopieren und `docker compose` auszuführen.
- **Firewall:** Ports 80, 443 (und ggf. 8080 für Geo-Manager-Status) sowie die Mesh-Ports müssen gemäß Betriebskonzept freigegeben sein.

---

## 3. Wichtige Daten auf den Server kopieren

### 3.1 Repository klonen

- Klonen Sie das Projekt in ein festes Verzeichnis, z. B. `/opt/agt-app-haproxy`.  
  Für Produktion empfiehlt sich ein bestimmter **Tag oder Branch** (z. B. `main` oder ein Release-Tag), damit alle Knoten dieselbe Version fahren.

```bash
sudo mkdir -p /opt
sudo git clone <URL-DES-REPOS> /opt/agt-app-haproxy
cd /opt/agt-app-haproxy
# Optional: auf einen bestimmten Stand wechseln
# git checkout v1.0.0
```

- **Warum ein festes Verzeichnis:** Pfade in `docker-compose.yaml` und in der Doku beziehen sich auf das Projektverzeichnis; ein einheitlicher Pfad vereinfacht Wartung und Skripte.

### 3.2 Rechte und Benutzer (optional)

- Wenn Sie nicht als root arbeiten: Stellen Sie sicher, dass Ihr Benutzer in der Gruppe `docker` ist, damit `docker compose` ohne sudo läuft.  
  Das Projektverzeichnis sollte für den betreibenden Benutzer les- und ausführbar sein; Schreibrechte werden nur für `conf/maps/`, `run/haproxy-stat` und ggf. Log-Verzeichnisse benötigt (siehe unten).

---

## 4. Externe Dateien und Verzeichnisse bereitstellen

Folgende Dinge liegen **nicht** im Repository und müssen pro Server bereitgestellt werden.

### 4.1 Umgebungsvariablen (`.env`)

- **Datei:** Im Projektroot: `.env`  
- **Erzeugen:** Kopie von `.env.example` anlegen und anpassen.

```bash
cp .env.example .env
chmod 600 .env
```

- **Pro Server anzupassen (Pflicht):**
  - **NODE_NAME:** Eindeutiger Knotenname, z. B. `agt-1`, `agt-2`, `agt-3`. Wird beim Start des HAProxy-Containers automatisch in die Config eingetragen (Platzhalter `__NODE_NAME__` → `localpeer`); die Datei `conf/haproxy.cfg` muss nicht manuell geändert werden.
  - **MESH_NODES:** Kommaseparierte Mesh-IPs **aller drei** Knoten, Reihenfolge = agt-1, agt-2, agt-3 (z. B. `172.20.0.1,172.20.0.2,172.20.0.3`). Wird für Geo-Manager (Cluster-Health) und im HAProxy-Entrypoint für die Peers-Zeilen genutzt (lokaler Knoten ohne IP, andere mit IP:50000).
  - **CLUSTER_MAXCONN:** Clusterweites Verbindungslimit (z. B. 200). Über eine Stick-Table mit konstantem Key „global“ und Peers-Synchronisation wird die Summe der Verbindungen über alle Knoten begrenzt; ab diesem Wert antwortet jeder Knoten mit 503 (Überlastungsschutz).
  - **NODE_PRIO:** `1` = Master (lädt Geo-Daten, schreibt Maps), `2` oder `3` = Follower (übernehmen Maps mit Verzögerung). Pro physischem Server genau einen Master (Prio 1) und zwei Follower (Prio 2 und 3).
  - **ANCHOR_IPS:** Kommaseparierte IPs, die in der Geo-Liste als DE/EU (oder erlaubte Länder) gelten **müssen**. Der Geo-Manager prüft vor Aktivierung einer neuen Map, dass diese IPs erlaubt sind (Plausibilitäts-Check). Fehlt eine Anchor-IP oder ist sie „blockiert“, wird die neue Map nicht aktiviert.
  - **GEO_SOURCE_URL:** URL der Geo-IP-CSV (eine Datei mit Spalten z. B. `network`, `country_iso_code`). Alternativ zwei URLs: `GEO_BLOCKS_URL` und `GEO_LOCATIONS_URL` (MaxMind-Style). Ohne gültige URL kann der Master keine Maps erzeugen.

- **Optional:** Mail-Benachrichtigung bei anhaltenden Fehlern (`MAIL_*`), Staged-Delays (`STAGE_DELAY_PRIO2_HOURS`, `STAGE_DELAY_PRIO3_HOURS`), Fetch-Intervall (`FETCH_INTERVAL_HOURS`). Alle Optionen sind in `.env.example` kommentiert.

- **Sicherheit:** `.env` enthält keine Passwörter im Klartext außer optional `MAIL_PASSWORD`; Datei nicht ins Repository committen und Zugriff beschränken (`chmod 600`).

### 4.2 TLS-Zertifikat für HAProxy (`ssl/haproxy.pem`)

- **Datei:** `ssl/haproxy.pem` im Projektroot.  
- **Automatische Erneuerung:** Siehe [Zertifikatserneuerung.md](Zertifikatserneuerung.md) (Let's Encrypt, Certbot, Deploy-Script auf alle Knoten) **oder** den neuen In-Cluster-Rollout über den cert-manager (siehe [Zertifikats-Rollout.md](Zertifikats-Rollout.md)).  
- **Inhalt:** Fullchain (Zertifikat + ggf. Zwischenzertifikate) und privater Schlüssel in **einer** Datei (übliches Format für HAProxy). Reihenfolge: zuerst Zertifikat(e), dann `-----BEGIN PRIVATE KEY-----` bzw. `-----BEGIN RSA PRIVATE KEY-----`.

- **Woher:** Ihre interne CA, Let's Encrypt oder anderer Zertifikatsanbieter. Auf dem Server nur die fertige PEM-Datei ablegen; Private Keys niemals ins Repo oder in unsichere Verzeichnisse legen.

```bash
mkdir -p ssl
# Option A (bestehend): PEM-Datei von sicherer Quelle nach ssl/haproxy.pem kopieren
chmod 600 ssl/haproxy.pem
```

- **Hinweis cert-manager:** Wenn der cert-manager im Cluster verwendet wird, schreibt dieser das kombinierte PEM automatisch nach `ssl/haproxy.pem` (bzw. den gemounteten Pfad). In diesem Fall muss die Datei nicht mehr manuell kopiert werden; stattdessen sorgt Certbot auf dem Masterknoten für frische Zertifikate, und der cert-manager verteilt sie im Cluster.

```bash
```

- **Warum 600:** Nur der Eigentümer soll den privaten Schlüssel lesen können; die Container mounten die Datei read-only.

### 4.3 Coraza-/OWASP-Regeln (`coraza/rules/`)

- **Verzeichnis:** `coraza/rules/`  
- **Inhalt:** Die WAF-Regeln (z. B. OWASP ModSecurity Core Rule Set). Ohne Regeln startet der Coraza-SPOA-Container ggf. mit leeren Regeln; für Produktion müssen hier die gewünschten Regeldateien liegen.

Beispiel (OWASP CRS aus GitHub):

```bash
git clone https://github.com/coreruleset/coreruleset.git coraza/rules
# Oder nur bestimmte Version/Unterverzeichnis übernehmen – je nach Projektvorgabe.
```

- **Hinweis:** Prüfen Sie die Projekt- bzw. Sicherheitsvorgaben, ob bestimmte CRS-Versionen oder Anpassungen vorgeschrieben sind.

### 4.4 HAProxy-Stat-Socket-Verzeichnis (`run/haproxy-stat`)

- **Verzeichnis:** `run/haproxy-stat`  
- **Zweck:** HAProxy und Geo-Manager teilen sich den Stats-Socket (`/var/run/haproxy-stat/socket`). Das Verzeichnis muss auf dem Host existieren und dem Benutzer gehören, unter dem HAProxy im Container läuft (Alpine-Image: oft `haproxy` mit UID 99).

```bash
mkdir -p run/haproxy-stat
sudo chown 99:99 run/haproxy-stat
```

- **Warum 99:99:** Im offiziellen HAProxy-Alpine-Image läuft HAProxy typischerweise als User mit UID 99; nur so kann der Prozess den Socket anlegen und der Geo-Manager (der das Verzeichnis schreibend gemountet bekommt) mit HAProxy kommunizieren. Wenn Ihr Image andere UIDs verwendet, anpassen.

### 4.5 Optionale Testdaten (nur für lokale Tests)

- Für **Produktion** nicht nötig. Wenn Sie lokal mit einer Geo-CSV-Datei testen wollen, legen Sie die CSV in `conf/test-data/` und setzen in `.env` z. B. `GEO_SOURCE_URL=file:///data/geoip2-ipv4.csv` (der Container mountet `conf/test-data` nach `/data`). Siehe README im Projektroot.

---

## 5. Prüfungen vor dem ersten Start

- **HAProxy-Konfiguration prüfen:** Damit beim Start kein sofortiger Fehler entsteht, die Konfiguration einmal testen (entweder mit lokal installiertem `haproxy` oder in einem einmalig gestarteten Container):

```bash
docker run --rm -v "$(pwd)/conf/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro" haproxy:3.2-alpine haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg
```

  Erwartung: Ausgabe „Configuration file is valid“.

- **`.env` prüfen:** Alle Pflichtvariablen gesetzt? Keine Tippfehler in `NODE_NAME`, `MESH_NODES`, `ANCHOR_IPS`, `GEO_SOURCE_URL` (bzw. `GEO_BLOCKS_URL` + `GEO_LOCATIONS_URL`).

- **Anchor-IPs:** Die in `ANCHOR_IPS` eingetragenen Adressen sollten in Ihrer Geo-Quelle tatsächlich dem erlaubten Raum (z. B. DE/EU) zugeordnet sein; sonst schlägt der Anchor-Check dauerhaft fehl und keine neue Map wird aktiviert.

- **Netzwerk:** Kurz prüfen, ob die anderen Mesh-Knoten von diesem Server aus erreichbar sind (z. B. `ping` oder `curl` auf die jeweiligen IPs/Ports), falls Sie Cluster-Health nutzen wollen.

---

## 6. Start des Stacks

- **Erster Start (mit Image-Build):**

```bash
cd /opt/agt-app-haproxy   # oder Ihr gewähltes Verzeichnis
docker compose up -d --build
```

- **Spätere Starts (ohne Build):**

```bash
docker compose up -d
```

- **Ablauf:** Compose startet die Dienste in Abhängigkeitsreihenfolge (coraza-spoa → haproxy → geo-manager). Der Geo-Manager (Master) lädt beim ersten Lauf die Geo-Daten, validiert sie, schreibt die Maps und löst einen HAProxy-Reload aus. Das kann beim ersten Mal etwas dauern.

- **Restart-Policy:** Die Dienste sind mit `restart: always` konfiguriert; bei Absturz oder Neustart des Servers starten die Container automatisch neu.

---

## 7. Überwachung und Betrieb

### 7.1 Container-Status

```bash
docker compose ps
```

  Alle drei Dienste (`haproxy_gateway`, `coraza-spoa`, `geo-manager`) sollten „Up“ sein. Bei „Restarting“ Logs prüfen (siehe unten).

### 7.2 Logs

- **Alle Dienste:**  
  `docker compose logs -f`

- **Nur Geo-Manager (wichtig für Validierung und Staged Rollout):**  
  `docker compose logs -f geo-manager`

- **Nur HAProxy:**  
  `docker compose logs -f haproxy`

  Bei Fehlern zuerst hier nachsehen; der Geo-Manager protokolliert z. B. Validierungsfehler, Anchor-Check-Fehler und Reload-Ergebnisse.

### 7.3 Health- und Status-Endpunkte (Geo-Manager)

- **Liveness (z. B. für Load-Balancer/Monitoring):**  
  `curl -s http://localhost:8080/health`  
  Erwartung: HTTP 200, Body „OK“.

- **Geo-Status (Validierung, Map-Version, Master/Follower):**  
  `curl -s http://localhost:8080/geo/status`  
  Liefert JSON mit u. a. `node_prio`, `validated_at`, `map_version`. Für Follower: Hier sehen Sie, ob sie bereits eine Map vom Master übernommen haben.

- **Cluster-Übersicht:**  
  `curl -s http://localhost:8080/cluster`  
  Zeigt die letzten Cluster-Probe-Ergebnisse (andere Knoten, Latenz, Offline-Infos).

- **Prometheus-Metriken:**  
  `curl -s http://localhost:8080/metrics`  
  Für Integration in Prometheus/Grafana.

(Port 8080 ist über `GEO_STATUS_PORT` in der `.env` änderbar.)

### 7.4 HAProxy-Statistik

- Die HAProxy-Statistik-Seite läuft im Container auf Port 56708 und ist auf `127.0.0.1` gebunden (`conf/haproxy.cfg`, `frontend stats`). Es gibt **kein** Port-Mapping in `docker-compose.yaml`; Zugriff erfolgt nur über den Host (z. B. SSH-Tunnel oder `docker exec`).
- Die URI ist in `conf/haproxy.cfg` hinterlegt, die Zugangsdaten werden **nur** über ENV gesetzt (`STATS_USER`, `STATS_PASSWORD` in `.env`).
- Beispiel:

  ```env
  STATS_USER=admin
  STATS_PASSWORD=<sehr-starkes-passwort>
  ```

- Zugriff z. B. per SSH-Tunnel:

  ```bash
  ssh -L 56708:127.0.0.1:56708 <admin>@<haproxy-host>
  ```

  Danach im Browser `http://localhost:56708/<stats-uri>` aufrufen. Dort sehen Sie Backend- und Frontend-Status.

### 7.5 Zentrales Cluster-Dashboard (cert-manager)

- Der `cert-manager` stellt ein kleines, read-only Dashboard unter `GET /dashboard` bereit (im gleichen internen Netzwerk wie HAProxy/Geo-Manager).
- Das Dashboard zeigt u. a.:
  - Knotenname und Priorität (`NODE_NAME`, `NODE_PRIO`)
  - Rolle (Zertifikats-Master oder Follower)
  - Aktive Zertifikatsversion (`version`-Hash) und „gültig seit“
  - Einen eingebetteten Status-Auszug des Geo-Managers (`/geo/status`)
  - Zwei Buttons:
    - **Deploy Zertifikat jetzt** – baut auf dem Zertifikats-Master ein neues `haproxy.pem` aus den Certbot-Dateien (`fullchain.pem`/`privkey.pem`).
    - **Deploy Geo-Listen jetzt** – triggert auf dem Geo-Master einen sofortigen Fetch/Validate/Activate-Lauf.
- Das Dashboard ist standardmäßig **nicht** nach außen gemappt; Zugriff erfolgt intern (z. B. per Port-Forwarding auf den `cert-manager`-Container oder einen nachgelagerten Admin-Proxy). Für den ersten Produktivtest bietet es eine zentrale Übersicht und manuelle „Deploy now“-Knöpfe.

### 7.5 Was tun bei Fehlern?

- **Container startet nicht / Restart-Schleife:**  
  Logs mit `docker compose logs <service>` prüfen. Häufige Ursachen: fehlende oder falsche `ssl/haproxy.pem`, fehlerhafte HAProxy-Config, fehlende oder falsche Rechte auf `run/haproxy-stat` (UID 99:99).

- **Geo-Manager aktiviert keine neue Map:**  
  In den Logs nach „validation“, „anchor“, „size“ suchen. Anchor-Check schlägt fehl, wenn eine `ANCHOR_IPS`-Adresse in der neuen Geo-Liste nicht als erlaubt gilt; Size-Check, wenn die neue Map deutlich kleiner ist als die alte (Schwellwert `SIZE_DEVIATION_THRESHOLD`). Behebung: Geo-Quelle oder ANCHOR_IPS/Threshold prüfen, keine flächendeckenden Änderungen ohne Test.

- **Follower übernehmen Map nicht:**  
  Follower warten 48h (Prio 2) bzw. 96h (Prio 3) nach erfolgreicher Validierung auf dem Master. Prüfen: `/geo/status` auf dem Master („validated_at“, „map_version“) und auf dem Follower; Netzwerk zwischen den Knoten (MESH_NODES, Firewall).

- **Kritische Änderungen (Config, .env, Zertifikat):**  
  Nach Änderungen `docker compose up -d` ausführen, damit die Container die neuen Dateien/Umgebungsvariablen verwenden. Bei HAProxy-Config-Änderungen führt der Geo-Manager bei seinem nächsten Zyklus einen Reload aus; bei sofortigem Bedarf kann ein manueller Reload über den HAProxy-Socket erfolgen (nur mit entsprechender Erfahrung und Vorsicht).

---

## 8. Kurz-Checkliste

- [ ] Repo in festes Verzeichnis (z. B. `/opt/agt-app-haproxy`) geklont, ggf. Tag/Branch gesetzt  
- [ ] `.env` aus `.env.example` erstellt, `NODE_NAME`, `NODE_PRIO`, `MESH_NODES`, `ANCHOR_IPS`, `GEO_SOURCE_URL` pro Server korrekt gesetzt  
- [ ] `ssl/haproxy.pem` (Fullchain + Privkey) vorhanden, Rechte 600  
- [ ] `coraza/rules/` mit WAF-Regeln (z. B. OWASP CRS) befüllt  
- [ ] `run/haproxy-stat` angelegt, `chown 99:99`  
- [ ] `haproxy -c -f conf/haproxy.cfg` erfolgreich (per Docker-Befehl)  
- [ ] `docker compose up -d --build` ausgeführt, alle drei Container „Up“  
- [ ] `curl http://localhost:8080/health` → 200 OK  
- [ ] `curl http://localhost:8080/geo/status` zeigt sinnvollen Status (Master: validated_at gesetzt nach erstem Lauf; Follower: Übernahme nach Ablauf der Staged-Delays)  
- [ ] Logs ohne dauerhafte Fehlermeldungen: `docker compose logs -f geo-manager` kurz beobachten  

---

Weitere Themen (Betrieb, Wartung, Architektur) können in eigenen Dateien im Ordner `Dokumentationen/` ergänzt werden.
