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
from .metrics import to_prometheus
from .state import get_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


class CertHandler(BaseHTTPRequestHandler):
    """Serves GET /health, /metrics, /cert/status, /cert/download and POST /cert/deploy-now."""

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
        if path == "/metrics":
            self._send_metrics()
            return
        if path == "/cert/download":
            if not self._is_authorized(parsed.query):
                self.send_error(403, "Forbidden")
                return
            self._send_download(parsed.query)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/cert/deploy-now":
            self._handle_cert_deploy_now()
            return
        self.send_error(404)

    def _is_authorized(self, query: str) -> bool:
        """Authorize mesh-internal API calls using CERT_CLUSTER_KEY if configured."""
        config: Config = self.server.config  # type: ignore[attr-defined]
        if not config.cluster_key:
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

    def _send_metrics(self) -> None:
        config: Config = self.server.config  # type: ignore[attr-defined]
        state = get_state()
        body = to_prometheus(config, state).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
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
        run_leader_once(config)
    else:
        logger.info(
            "cert-manager starting as follower on port %s (node_prio=%s)",
            config.status_port,
            config.node_prio,
        )
        run_follower_once(config)
        t = threading.Thread(target=run_follower_loop, args=(config,), daemon=True)
        t.start()

    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
