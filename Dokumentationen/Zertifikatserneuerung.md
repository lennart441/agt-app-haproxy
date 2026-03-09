# Zertifikatserneuerung (Let's Encrypt + Deployment auf alle HAProxy-Knoten)

Diese Anleitung beschreibt, wie Sie TLS-Zertifikate per Let's Encrypt (Certbot) mit Cloudflare-DNS erneuern und automatisch auf alle drei HAProxy-Knoten deployen. Das zugehörige Script liegt im Projekt: `scripts/deploy-haproxy-certs.sh`.

**Wichtig:** Das komplette Setup (Certbot, Cron, Deploy-Script) läuft nur auf **einem** Server – typischerweise dem Master-Knoten (AGT1). Die anderen Knoten erhalten die Zertifikate per SSH/SCP vom Script.

---

## 1. Übersicht

Ein **einziger Cron-Job** führt einmal am Tag **deploy-haproxy-certs.sh** aus. Das Script macht in dieser Reihenfolge:

1. **certbot renew** – erneuert die Let's-Encrypt-Zertifikate lokal (in `/etc/letsencrypt/live/…`), falls sie fällig sind.
2. **Deployment** – prüft, ob sich das Zertifikat geändert hat (MD5); wenn ja: PEM bauen, per SCP auf alle Knoten kopieren, HAProxy-Config prüfen, hitless Reload (SIGUSR2), Health prüfen. Bei Fehlern bricht das Script ab (kritische Infrastruktur).

Certbot und das Script laufen nur auf **einem** Server (z. B. AGT1); die anderen Knoten erhalten die Zertifikate per SSH/SCP.

---

## 2. Voraussetzungen

- Ein Server (z. B. AGT1) mit Root- oder sudo-Zugang für Certbot und Cron.
- **Domain** für die Zertifikate (z. B. `agt-app.de` inkl. Wildcard `*.agt-app.de`).
- **Cloudflare** als DNS-Provider; ein API-Token mit Berechtigung „DNS Edit“ für die Zone.
- Auf allen drei HAProxy-Knoten: das Projekt installiert (z. B. unter `/opt/agt-app-haproxy`), Container-Name `haproxy_gateway`, Verzeichnis `ssl/` vorhanden.
- **SSH-Zugang** vom Certbot-Server zu allen drei Knoten (keybasiert, ohne Passwort), damit das Deploy-Script kopieren und Befehle ausführen kann.

---

## 3. Certbot und Cloudflare-DNS einrichten (einmalig, nur auf einem Server)

### 3.1 Pakete installieren

```bash
apt update
apt install -y certbot python3-certbot-dns-cloudflare
```

### 3.2 Cloudflare-API-Token ablegen

Erstellen Sie eine Datei mit dem API-Token (z. B. im Home des Benutzers, der Certbot ausführt):

```bash
nano ~/certbot-creds.ini
```

Inhalt (Token durch Ihren echten Cloudflare API Token ersetzen):

```ini
# Cloudflare API token used by Certbot
dns_cloudflare_api_token = IHR_CLOUDFLARE_API_TOKEN
```

Sicherheit:

```bash
chmod 600 ~/certbot-creds.ini
```

Diese Datei wird nur von Certbot gelesen; sie **nicht** ins Projekt-Repository legen.

### 3.3 Erstes Zertifikat holen

```bash
certbot certonly \
  --dns-cloudflare \
  --dns-cloudflare-credentials ~/certbot-creds.ini \
  --dns-cloudflare-propagation-seconds 60 \
  -d agt-app.de \
  -d "*.agt-app.de"
```

Domain(s) anpassen. Certbot speichert die Zertifikate unter `/etc/letsencrypt/live/<domain>/` (z. B. `fullchain.pem`, `privkey.pem`).

Zertifikate anzeigen:

```bash
certbot certificates
```

---

## 4. SSH-Zugang vom Certbot-Server zu allen HAProxy-Knoten

Das Deploy-Script muss per SCP/SSH auf alle drei Knoten zugreifen können – ohne Passwortabfrage.

### 4.1 SSH-Key erzeugen (falls noch nicht vorhanden)

Auf dem Server, auf dem Certbot und das Deploy-Script laufen (z. B. AGT1):

```bash
ssh-keygen -t ed25519 -C "haproxy-cert-deploy"
# Kein Passwort eingeben (leer lassen), Standardpfad ~/.ssh/id_ed25519
```

### 4.2 Public Key auf alle Knoten kopieren

Ersetzen Sie die IPs durch Ihre Mesh-IPs (z. B. WireGuard):

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@172.20.0.1
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@172.20.0.2
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@172.20.0.3
```

Test:

```bash
ssh -i ~/.ssh/id_ed25519 root@172.20.0.1 "hostname"
```

(dasselbe für .2 und .3).

---

## 5. Deploy-Script konfigurieren

### 5.1 Konfigurationsdatei anlegen

Im Projektverzeichnis (z. B. wo Sie das Repo auf dem Certbot-Server geklont haben, oder in einem festen Installationspfad):

```bash
cd /opt/agt-app-haproxy   # oder Ihr Pfad
cp scripts/cert-deploy.env.example scripts/cert-deploy.env
chmod 600 scripts/cert-deploy.env
nano scripts/cert-deploy.env
```

### 5.2 Werte anpassen

| Variable | Bedeutung | Beispiel |
|----------|-----------|----------|
| `CERT_DOMAIN` | Domain für Let's Encrypt | `agt-app.de` |
| `CERT_LE_DIR` | Pfad zu den Live-Zertifikaten | `/etc/letsencrypt/live/agt-app.de` |
| `CERT_SERVERS` | Komma-getrennte Liste der Knoten-IPs | `172.20.0.1,172.20.0.2,172.20.0.3` |
| `CERT_SSH_USER` | SSH-Benutzer auf den Zielservern | `root` |
| `CERT_SSH_KEY` | Pfad zum privaten SSH-Key | `/root/.ssh/id_ed25519` |
| `CERT_TARGET_DIR` | **Wichtig:** `ssl`-Ordner auf jedem Knoten. Muss dem Installationspfad entsprechen. | `/opt/agt-app-haproxy/ssl` |
| `CERT_CONTAINER_NAME` | HAProxy-Container-Name | `haproxy_gateway` |
| `CERT_ADMIN_EMAIL` | Optional: E-Mail bei Fehlern/Erfolg (wenn `mail` installiert ist) | `admin@example.com` |

**CERT_TARGET_DIR:** Auf jedem der drei Server muss das Projekt im gleichen Pfad liegen (z. B. `/opt/agt-app-haproxy`). Dann ist `CERT_TARGET_DIR=/opt/agt-app-haproxy/ssl`. Wenn Sie das Projekt woanders installiert haben, passen Sie den Pfad an.

### 5.3 Script ausführbar machen und einmal testen

```bash
chmod +x scripts/deploy-haproxy-certs.sh
./scripts/deploy-haproxy-certs.sh
```

Erwartung: Log-Ausgabe, Zertifikat wird auf alle drei Knoten kopiert, pro Knoten Config-Check und Reload, am Ende „Deployment auf allen Servern erfolgreich abgeschlossen“. Bei Fehlern bricht das Script ab und gibt eine klare Meldung aus (z. B. SCP fehlgeschlagen, Config-Check fehlgeschlagen, Container nicht healthy).

---

## 6. Cron: Einmal am Tag

Ein Cron-Job führt das Script einmal täglich aus. Das Script erledigt intern **certbot renew** und anschließend das Deployment (nur bei geänderter Zertifikatsdatei).

```bash
crontab -e
```

Eintrag (z. B. täglich 3:00 Uhr; Pfad an Ihren Installationspfad anpassen):

```cron
0 3 * * * /opt/agt-app-haproxy/scripts/deploy-haproxy-certs.sh
```

Mehr ist nicht nötig – kein zweiter Cron, kein Certbot-Hook.

---

## 7. Ablauf des Deploy-Scripts (Kurz)

1. **certbot renew:** Zertifikate werden erneuert, falls fällig (Certbot aktualisiert die Dateien unter `/etc/letsencrypt/live/…`).
2. **Prüfung:** Sind `fullchain.pem` und `privkey.pem` unter `CERT_LE_DIR` vorhanden?
3. **Änderungserkennung:** MD5 von `fullchain.pem` wird mit der letzten deployten Version verglichen (`/var/log/haproxy_cert_last_md5.txt`). Keine Änderung → Script beendet sich ohne Deployment.
4. **PEM bauen:** Fullchain und Privkey werden zu einer temporären `haproxy.pem` zusammengefügt (HAProxy-Format).
5. **Pro Knoten (sequenziell):**
   - SCP: `haproxy.pem` → `CERT_TARGET_DIR/haproxy.pem`
   - SSH: `docker exec haproxy_gateway haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg`
   - SSH: `docker kill -s USR2 haproxy_gateway` (hitless Reload)
   - 15 Sekunden warten, dann Docker-Health (oder Status) prüfen
   - Bei Fehler: Script bricht ab, keine weiteren Knoten.
6. **Abschluss:** MD5 speichern, temporäre Datei löschen, optional E-Mail bei Erfolg.

So werden fehlerhafte Zertifikate oder Configs nicht auf alle Knoten verteilt; der erste fehlschlagende Knoten stoppt den Lauf.

---

## 8. Fehlerbehebung

- **„Let's Encrypt Zertifikate nicht gefunden“:** `CERT_LE_DIR` prüfen (z. B. `ls /etc/letsencrypt/live/agt-app.de`). Certbot muss mindestens einmal erfolgreich ausgeführt worden sein.
- **SCP fehlgeschlagen:** SSH-Verbindung testen (`ssh -i … root@IP hostname`), Firewall, `CERT_SSH_KEY` und `CERT_SSH_USER`, `CERT_TARGET_DIR` (muss auf dem Ziel existieren und beschreibbar sein).
- **Config Check fehlgeschlagen:** Auf dem betroffenen Knoten `docker exec haproxy_gateway haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg` prüfen. Oft: Zertifikat-Format oder fehlerhafte HAProxy-Config.
- **Container nicht healthy:** Nach Reload Logs prüfen (`docker compose logs haproxy`). Bei dauerhaftem Problem: altes Zertifikat wiederherstellen, Config prüfen, dann erneut deployen.
- **Script findet SSH-Key nicht:** `CERT_SSH_KEY` in `cert-deploy.env` explizit setzen (z. B. `/root/.ssh/id_ed25519`).

---

## 9. Sicherheitshinweise

- `cert-deploy.env` und `~/certbot-creds.ini` enthalten sensible Daten (API-Token, Pfade). Nicht ins Repo committen; `scripts/cert-deploy.env` ist in `.gitignore`.
- SSH-Key nur für Cert-Deploy verwenden (eigener Key mit Kommentar „haproxy-cert-deploy“), Berechtigungen z. B. `600` für private Keys.
- Optional: `CERT_ADMIN_EMAIL` setzen und `mail` installieren, um bei Fehlern benachrichtigt zu werden.

---

Zusammenfassung: Certbot (mit Cloudflare-DNS) und SSH-Zugang einmal einrichten, `cert-deploy.env` anpassen, Deploy-Script einmal testen, einen Cron-Job eintragen (einmal am Tag) – danach erledigt das Script Erneuerung und Deployment.
