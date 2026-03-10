"""
cert-manager: HTTP server for certificate status and background loops.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from .config import Config
from .follower import run_follower_loop, run_follower_once
from .leader import run_leader_once
from .state import get_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


import http.client
import os


class CertHandler(BaseHTTPRequestHandler):
    """Serves GET /health, /cert/status, /cert/download, /dashboard and POST triggers."""

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send_health()
            return
        if path == "/cert/status":
            if not self._is_authorized(parsed.query):
                self.send_error(403, "Forbidden")
                return
            self._send_status()
            return
        if path == "/cert/download":
            if not self._is_authorized(parsed.query):
                self.send_error(403, "Forbidden")
                return
            self._send_download(parsed.query)
            return
        if path == "/dashboard":
            self._send_dashboard()
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/cert/deploy-now":
            self._handle_cert_deploy_now()
            return
        if path == "/geo/deploy-now":
            self._handle_geo_deploy_now()
            return
        self.send_error(404)

    def _is_authorized(self, query: str) -> bool:
        """Authorize mesh-internal API calls using CERT_CLUSTER_KEY if configured."""
        config: Config = self.server.config  # type: ignore[attr-defined]
        if not config.cluster_key:
            # No key configured → auth disabled (backwards compatible).
            return True
        params = parse_qs(query)
        provided = params.get("cluster_key", [""])[0]
        return provided == config.cluster_key

    def _send_health(self) -> None:
        body = b"OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_status(self) -> None:
        config: Config = self.server.config  # type: ignore[attr-defined]
        state = get_state()
        payload = {
            "node_prio": config.node_prio,
            "node_name": config.node_name,
            "cert_is_master": config.cert_is_master,
            "version": state.version if state else None,
            "validated_since": state.validated_since.isoformat() if state else None,
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_download(self, query: str) -> None:
        config: Config = self.server.config  # type: ignore[attr-defined]
        state = get_state()
        if not state:
            self.send_error(404, "No certificate active")
            return
        params = parse_qs(query)
        requested_version = params.get("version", [state.version])[0]
        if requested_version != state.version:
            self.send_error(404, "Unknown version")
            return
        try:
            with open(config.target_pem_path, "rb") as f:
                pem = f.read()
        except FileNotFoundError:
            self.send_error(404, "PEM not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-pem-file")
        self.send_header("Content-Length", str(len(pem)))
        self.end_headers()
        self.wfile.write(pem)

    def _send_dashboard(self) -> None:
        """GET /dashboard: simple read-only HTML overview with manual actions."""
        config: Config = self.server.config  # type: ignore[attr-defined]
        state = get_state()
        cert_version = state.version if state else "—"
        cert_since = state.validated_since.isoformat() if state else "—"

        # Try to fetch local geo-manager status (best-effort, mesh-internal).
        geo_status_text = "unbekannt"
        geo_port = int(os.environ.get("GEO_STATUS_PORT", "8080"))
        try:
            conn = http.client.HTTPConnection("geo-manager", geo_port, timeout=2.0)
            conn.request("GET", "/geo/status")
            resp = conn.getresponse()
            if resp.status == 200:
                body = resp.read().decode("utf-8", errors="ignore")
                geo_status_text = body
            else:
                geo_status_text = f"HTTP {resp.status}"
            conn.close()
        except Exception:
            geo_status_text = "nicht erreichbar"

        html = f"""
<!doctype html>
<html lang="de">
  <head>
    <meta charset="utf-8">
    <title>HA-Cluster Dashboard</title>
    <style>
      body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #0b1120; color: #e5e7eb; }}
      h1 {{ font-size: 1.8rem; margin-bottom: 1rem; }}
      h2 {{ font-size: 1.3rem; margin-top: 1.5rem; }}
      .card {{ background: #020617; border-radius: 0.75rem; padding: 1.5rem; margin-bottom: 1.5rem;
               border: 1px solid #1f2937; box-shadow: 0 10px 25px rgba(0,0,0,0.4); }}
      .grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(260px,1fr)); gap: 1.5rem; }}
      .badge {{ display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px; font-size: 0.75rem; }}
      .badge-master {{ background: #0f766e33; color: #6ee7b7; border: 1px solid #0f766e; }}
      .badge-follower {{ background: #1d4ed833; color: #93c5fd; border: 1px solid #1d4ed8; }}
      .meta {{ color: #9ca3af; font-size: 0.9rem; }}
      pre {{ background: #020617; border-radius: 0.5rem; padding: 0.75rem; overflow-x: auto;
             border: 1px solid #111827; max-height: 260px; }}
      button {{ background: linear-gradient(to right,#22c55e,#16a34a); border: none; color: #011;
               padding: 0.5rem 0.9rem; border-radius: 0.5rem; cursor: pointer;
               font-weight: 600; font-size: 0.9rem; }}
      button.danger {{ background: linear-gradient(to right,#f97316,#ea580c); color: #111827; }}
      button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
      form {{ margin-top: 0.75rem; }}
      a {{ color: #38bdf8; text-decoration: none; }}
      a:hover {{ text-decoration: underline; }}
    </style>
  </head>
  <body>
    <h1>HA-Cluster Dashboard</h1>
    <p class="meta">
      Knoten <strong>{config.node_name}</strong> (Prio {config.node_prio}) –
      Rolle: {"<span class='badge badge-master'>Master</span>" if config.cert_is_master else "<span class='badge badge-follower'>Follower</span>"}.
      Dieses Dashboard ist read-only bis auf die expliziten "Deploy now"-Aktionen unten.
    </p>

    <div class="grid">
      <section class="card">
        <h2>Zertifikate (cert-manager)</h2>
        <p class="meta">Aktive Version: <code>{cert_version}</code><br>
           Gültig seit: <code>{cert_since}</code></p>
        {"<p>Nur der Zertifikats-Master kann ein neues PEM aus den Certbot-Dateien bauen.</p>" if config.cert_is_master else "<p>Dieser Knoten ist Follower und übernimmt Zertifikate nur nach Staged-Delay.</p>"}
        <form method="POST" action="/cert/deploy-now">
          <button type="submit" {"class='danger'" if config.cert_is_master else "disabled class='danger'"}{" disabled" if not config.cert_is_master else ""}>
            Deploy Zertifikat jetzt
          </button>
        </form>
      </section>

      <section class="card">
        <h2>Geo-IP-Listen (Geo-Manager)</h2>
        <p class="meta">
          Status-Auszug von <code>geo-manager:{geo_port}</code>:
        </p>
        <pre>{geo_status_text}</pre>
        <form method="POST" action="/geo/deploy-now">
          <button type="submit" class="danger">
            Deploy Geo-Listen jetzt
          </button>
        </form>
      </section>
    </div>
  </body>
</html>
""".strip().encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _handle_cert_deploy_now(self) -> None:
        """POST /cert/deploy-now: rebuild PEM on master from Certbot files."""
        config: Config = self.server.config  # type: ignore[attr-defined]
        if not config.am_i_master():
            self.send_error(403, "Only cert-master may deploy certificate")
            return
        ok = run_leader_once(config)
        if not ok:
            self.send_error(500, "Failed to build or write certificate")
            return
        body = b"OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_geo_deploy_now(self) -> None:
        """POST /geo/deploy-now: trigger immediate Geo-Deploy via geo-manager."""
        geo_port = int(os.environ.get("GEO_STATUS_PORT", "8080"))
        try:
            conn = http.client.HTTPConnection("geo-manager", geo_port, timeout=5.0)
            conn.request("POST", "/geo/deploy-now")
            resp = conn.getresponse()
            body = resp.read()
            status = resp.status
            conn.close()
        except Exception as exc:  # pragma: no cover - Netzwerkfehler im Betrieb
            msg = f"Geo-Manager not reachable: {exc}".encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return

        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        logger.debug("%s - %s", self.address_string(), fmt % args)


def main() -> None:
    config = Config.from_env()
    server = HTTPServer(("0.0.0.0", config.status_port), CertHandler)
    server.config = config  # type: ignore[attr-defined]

    def _shutdown(_signum=None, _frame=None) -> None:
        logger.info("Received signal, shutting down gracefully")
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    if config.am_i_master():
        logger.info(
            "cert-manager starting as master on port %s (node_prio=%s)",
            config.status_port,
            config.node_prio,
        )
        # Initial run at startup; further invocations could be driven by cron/certbot hooks.
        run_leader_once(config)
    else:
        logger.info(
            "cert-manager starting as follower on port %s (node_prio=%s)",
            config.status_port,
            config.node_prio,
        )
        # Einmalig sofort versuchen, PEM vom Master zu holen (damit HAProxy beim Start sie hat)
        run_follower_once(config)
        t = threading.Thread(target=run_follower_loop, args=(config,), daemon=True)
        t.start()

    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()

