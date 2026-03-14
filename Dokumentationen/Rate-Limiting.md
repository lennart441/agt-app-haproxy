# Rate-Limiting auf HAProxy-Ebene (cluster-weit)

Dieses Dokument beschreibt die zwei Rate-Limit-Schichten auf HAProxy-Ebene, die **vor** den Backend-APIs greifen. Beide gelten **global über alle drei HAProxy-Knoten** (Peer-Synchronisation via `agt_cluster`).

---

## 1. Warum Rate-Limiting auf HAProxy-Ebene?

Die APIs limitieren selbst auf IP-/Mail-Basis. Ohne vorgelagertes Edge-Limit entstehen zwei Probleme:

**Problem 1 – Einzelner Angreifer (per-IP):**
Ein Angreifer sendet hohen Traffic an eine API. Die API antwortet ab dem Limit mit 429, aber die **Last liegt trotzdem auf dem Backend**. Das Backend wird überlastet, Health-Checks schlagen fehl, HAProxy markiert das Backend als DOWN → alle legitimen Nutzer sind ausgesperrt.

**Problem 2 – Backend-Überlastung (global):**
Auch ohne Angriff kann die Gesamtlast aller Clients ein Backend überlasten. Eine Sync-Antwort, die 30 Sekunden braucht, ist veraltet und wertlos. Besser: Request sofort mit einem Overload-Signal ablehnen → Clients erhöhen ihr Intervall → Last sinkt → API erholt sich (selbstheilend).

---

## 2. Architektur (zwei Schichten)

```
HAProxy (Edge, cluster-weit)
│
├── 1. Per-IP Rate-Limiting (sc2)           → 429 Too Many Requests
│   Schützt vor einzelnen Missbrauchern.
│   Identische Limits wie die APIs.
│
├── 2. Globaler Backend-Überlastungsschutz (sc3) → 503 Overload
│   Schützt vor Gesamt-Überlastung (alle IPs zusammen).
│   Clients können reagieren (Intervall erhöhen).
│
├── WAF / Coraza
├── Geo-Blocking
└── Routing → Backend

API (Application)
├── Per-IP Rate-Limiting (Defense-in-Depth)
├── Per-Mail Rate-Limiting (nur hier möglich)
└── Business-Logic-Validation
```

**Verarbeitungsreihenfolge im Frontend:**
1. WAF Auto-Ban (sc0)
2. Globales Verbindungslimit (sc1)
3. Per-IP Rate-Limiting (sc2) → Deny 429
4. Globaler Überlastungsschutz (sc3) → Return 503
5. WAF (Coraza SPOE)
6. Geo-Check
7. Backend-Routing

**Wichtig:** sc3-Tracking liegt **nach** dem per-IP-Deny. Bereits per-IP geblockte Requests zählen nicht zum globalen Zähler. Dadurch blähen Angreifer, die per-IP geblockt werden, den globalen Counter nicht auf.

**Cluster-weite Synchronisation:** Alle Stick-Tables werden über den `agt_cluster` Peers-Verbund synchronisiert. Limits gelten über alle drei Knoten hinweg, nicht pro einzelnem HAProxy.

---

## 3. Schicht 1 – Per-IP Rate-Limiting

### 3.1 API-Endpoints

| Endpoint | Route-ID | Zeitfenster | Max/IP | Stick-Table |
|----------|----------|-------------|--------|-------------|
| **Sync-API** (`/v3/sync-api`) | `api_sync` | 300 s | 1500 | `st_rl_api_sync` |
| **Primaer-API** Standard (`/v3/pri-api/*`) | `api_primaer` | 300 s | 120 | `st_rl_api_primaer` |
| **Primaer-API** request-code | `api_primaer_reqcode` | 300 s | 30 | `st_rl_api_primaer_reqcode` |
| **Primaer-API** verify-code | `api_primaer_verify` | 600 s | 20 | `st_rl_api_primaer_verify` |
| **AGT-Get-API** (`/v3/agt-get-api`) | `api_get` | 300 s | 20 | `st_rl_api_get` |
| **Report-API** (`/v3/report`) | `api_report` | 60 s | 10 | `st_rl_api_report` |

### 3.2 Statische Inhalte

| Endpoint | Route-ID | Zeitfenster | Max/IP | Stick-Table |
|----------|----------|-------------|--------|-------------|
| **Website** | `website` | 1 s | 2000 | `st_rl_website` |
| **Client** | `client` | 1 s | 2000 | `st_rl_client` |

### 3.3 Health/Ready-Endpoints (Monitoring)

| Endpoint | Route-ID | Zeitfenster | Max/IP | Stick-Table |
|----------|----------|-------------|--------|-------------|
| **Health/Ready** (`/v3/*/health`, `/v3/*/ready`) | `health_ready` | 60 s | 100 | `st_rl_health` |

Health- und Ready-Endpoints aller APIs (`/v3/sync-api/ready`, `/v3/agt-get-api/health` etc.) haben ein **eigenes, großzügiges Limit**. Sie zählen **nicht** zum Standard-per-IP-Limit des jeweiligen Endpoints und **nicht** zum globalen Überlastungsschutz (sc3). Dadurch kann Uptime Kuma (oder anderes Monitoring) diese Endpoints prüfen, ohne das Budget für legitime API-Requests zu belasten.

**Berechnung:** Uptime Kuma erzeugt ca. 9 req/min cluster-weit (3 Knoten × 3 req/min). Limit 100/60s = ca. 1000 % Puffer.

**Weiterhin aktiver Schutz:** Geo-Blocking, WAF, globales Verbindungslimit (sc1) gelten auch für Health/Ready-Requests.

### 3.4 Sub-Route-Logik (Primaer-API)

Requests an `/v3/pri-api/request-code` und `/v3/pri-api/verify-code` werden **nur** gegen ihr spezifisches Limit geprüft (nicht zusätzlich gegen das allgemeine Primaer-Limit). Alle anderen `/v3/pri-api/*`-Requests fallen unter das allgemeine Primaer-Limit (120/300s).

### 3.5 Vergleich HAProxy vs. API-Limits

| Endpoint | HAProxy (pro IP) | API (pro IP) | Bemerkung |
|----------|-----------------|--------------|-----------|
| Sync-API | 1500/300s | 1500/300s | Identisch |
| Primaer Standard | 120/300s | 120/300s | Identisch |
| request-code (IP) | 30/300s | 30/300s | Identisch; per-Mail nur in API (1/120s) |
| verify-code (IP) | 20/600s | 20/600s | Identisch; per-Mail nur in API (5/600s) |
| AGT-Get-API | 20/300s | 20/300s | Identisch |
| Report-API | 10/60s | 10/60s | Identisch |

**Per-Mail-Limits** (request-code: 1/120s pro Mail, verify-code: 5/600s pro Mail) bleiben ausschließlich in den APIs. HAProxy kann JSON-Bodies nicht zuverlässig parsen; das per-IP-Limit auf Edge-Ebene entschärft den Angriffsvektor bereits ausreichend.

### 3.6 Response bei Überschreitung

**HTTP 429 Too Many Requests** – Errorfile `conf/errors/429-rate-limit.http`:

```json
{
  "error": "rate_limit_exceeded",
  "category": "edge_rate_limit",
  "message": "Zu viele Anfragen. Bitte versuchen Sie es spaeter erneut."
}
```

---

## 4. Schicht 2 – Globaler Backend-Überlastungsschutz

### 4.1 Zweck

Schützt jedes API-Backend vor Überlastung durch die **Gesamtlast aller IPs zusammen**. Das Limit definiert, wie viele Requests/Sekunde ein Backend **insgesamt** verarbeiten soll. Darüber hinausgehende Requests werden sofort mit 503 + Overload-Signal abgelehnt.

**Vorteile:**
- Backend antwortet immer schnell (keine veralteten Responses)
- Clients erhalten ein klares Signal und können reagieren (z.B. Sync-Intervall erhöhen)
- Selbstheilend: Clients drosseln → Last sinkt → API erholt sich
- Unabhängig davon, ob die Last von einem Angreifer oder vielen legitimen Nutzern kommt

### 4.2 Limits pro Backend

| Backend | Backend-ID | Max. RPS (global) | Stick-Table |
|---------|-----------|-------------------|-------------|
| **Sync-API** | `api_sync` | 200 | `st_overload` |
| **Primaer-API** (alle Sub-Routen) | `api_primaer` | 100 | `st_overload` |
| **AGT-Get-API** | `api_get` | 50 | `st_overload` |
| **Report-API** | `api_report` | 30 | `st_overload` |

Alle Backends teilen sich eine Stick-Table (`st_overload`), aber mit unterschiedlichen Schlüsseln (Backend-ID als String). Website und Client haben kein Overload-Limit (statische Inhalte). **Health/Ready-Requests** (`/v3/*/health`, `/v3/*/ready`) sind vom Overload-Tracking ausgenommen und zählen nicht zum globalen Zähler.

**Primaer Sub-Routen:** request-code, verify-code und alle anderen Primaer-Pfade zählen zusammen als `api_primaer`. Das Overload-Limit gilt für die Gesamtlast auf das Primaer-Backend.

### 4.3 Response bei Überlastung

**HTTP 503** mit `Retry-After: 5` Header und JSON-Body (`conf/errors/503-overload.json`):

```json
{
  "error": "backend_overload",
  "category": "overload",
  "message": "Service voruebergehend ueberlastet. Bitte Anfrage-Frequenz reduzieren.",
  "retry_after_seconds": 5
}
```

**Client-Reaktion (empfohlen):**
- Sync-Client: `category === "overload"` erkennen → Sync-Intervall temporär erhöhen (z.B. 2s → 3–5s)
- Dashboard: Overload-Hinweis anzeigen, Retry nach `retry_after_seconds`
- Automatische Rückkehr zum normalen Intervall, wenn keine 503 mehr kommen

---

## 5. Konfigurationsdateien

| Datei | Inhalt |
|-------|--------|
| `conf/conf.d/00-global.cfg` | `tune.stick-counters 4` (sc0–sc3 aktivieren) |
| `conf/conf.d/30-stick-tables.cfg` | Stick-Table-Definitionen (per-IP + `st_overload` für global) |
| `conf/conf.d/50-frontend-https.cfg` | ACLs, Tracking-Regeln (sc2 per-IP, sc3 global), Deny/Return |
| `conf/maps/rate-limits.map` | Route-ID → Max. Requests pro Zeitfenster (per-IP) |
| `conf/maps/overload-limits.map` | Backend-ID → Max. RPS global (Überlastungsschutz) |
| `conf/errors/429-rate-limit.http` | HTTP-Response bei per-IP Rate-Limit |
| `conf/errors/503-overload.json` | JSON-Body bei Backend-Überlastung |

### rate-limits.map (per-IP)

```
api_get             20       # 20 req / 300s
api_sync            1500     # 1500 req / 300s
api_report          10       # 10 req / 60s
api_primaer         120      # 120 req / 300s
api_primaer_reqcode 30       # 30 req / 300s
api_primaer_verify  20       # 20 req / 600s
health_ready        100      # 100 req / 60s (Monitoring-Puffer)
website             2000     # 2000 req/s
client              2000     # 2000 req/s
```

### overload-limits.map (global)

```
api_sync    200     # 200 req/s gesamt
api_primaer 100     # 100 req/s gesamt
api_get     50      # 50 req/s gesamt
api_report  30      # 30 req/s gesamt
```

---

## 6. Ablauf einer Request

1. Request kommt auf einem der drei HAProxy-Knoten an.
2. **Per-IP Tracking (sc2):** Quell-IP wird in der passenden Stick-Table gezählt.
3. **Per-IP Check:** Liegt die IP über ihrem Limit? → **429** (Request endet hier).
4. **Global Tracking (sc3):** Request zählt zum globalen Backend-Zähler (nur wenn per-IP OK).
5. **Overload Check:** Liegt das Backend über dem globalen Limit? → **503** mit Overload-Signal.
6. **WAF + Geo + Routing:** Request wird an das Backend weitergeleitet.

---

## 7. Limits ändern

### Per-IP-Limit anpassen

Wert in `conf/maps/rate-limits.map` ändern → HAProxy-Reload. Alternativ zur Laufzeit:

```bash
echo "set map /usr/local/etc/haproxy/maps/rate-limits.map api_sync 2000" | socat stdio /var/run/haproxy-stat/socket
```

### Overload-Limit anpassen

Wert in `conf/maps/overload-limits.map` ändern → HAProxy-Reload. Alternativ zur Laufzeit:

```bash
echo "set map /usr/local/etc/haproxy/maps/overload-limits.map api_sync 300" | socat stdio /var/run/haproxy-stat/socket
```

### Zeitfenster anpassen (per-IP)

Stick-Table in `conf/conf.d/30-stick-tables.cfg` ändern (z.B. `http_req_rate(300s)` → `http_req_rate(600s)`). Erfordert HAProxy-Reload. Bei Fensteränderung auch `expire` anpassen (mindestens Fenster + 10s).

### Neuen Endpoint hinzufügen

1. Stick-Table in `30-stick-tables.cfg` anlegen (per-IP)
2. ACL + `track-sc2` + `set-var(txn.rl_id)` in `50-frontend-https.cfg`
3. Eintrag in `rate-limits.map`
4. Optional: `track-sc3` + `set-var(txn.ol_id)` + Eintrag in `overload-limits.map`
5. Diese Dokumentation aktualisieren

---

## 8. Stick-Counter-Belegung

| Slot | Zweck | Tracking-Key | Tabelle |
|------|-------|-------------|---------|
| **sc0** | WAF Auto-Ban | `src` (IP) | `st_waf_blocks` |
| **sc1** | Globales Verbindungslimit | `str(global)` | `st_global_conn` |
| **sc2** | Per-IP Rate-Limiting | `src` (IP) | `st_rl_health`, `st_rl_api_*`, `st_rl_website`, `st_rl_client` |
| **sc3** | Backend-Überlastungsschutz | `str(<backend_id>)` | `st_overload` (ohne Health/Ready) |

Aktiviert durch `tune.stick-counters 4` in `00-global.cfg`.

---

## 9. Was HAProxy nicht abdeckt (bleibt in der API)

| Limit-Typ | Grund |
|-----------|-------|
| **Per-Mail** (request-code, verify-code) | Erfordert JSON-Body-Parsing; in HAProxy nicht zuverlässig umsetzbar |
| **Session-basiertes Limiting** | HAProxy kennt keine API-Sessions/Tokens |
| **Fail-Close bei Redis-Ausfall** | HAProxy nutzt eigene Stick-Tables, kein Redis |

Diese Limits bleiben als zweite Verteidigungslinie in den APIs bestehen.
