"""
Dashboard HTTP server: static file serving + JSON API endpoints.

All data-heavy responses are fetched client-side via JavaScript fetch() calls,
so the HTML shell loads instantly regardless of cluster reachability.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
import signal
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from .config import Config
from .docker_client import DockerClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".json": "application/json",
}

_ALLOWED_CONTAINER_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


class DashboardHandler(BaseHTTPRequestHandler):
    """Serves static files and JSON API endpoints."""

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        query = self.path.split("?", 1)[1] if "?" in self.path else ""

        if path == "/" or path == "/index.html":
            self._serve_static("index.html")
            return
        if path == "/health":
            self._send_json_ok({"status": "ok"})
            return
        if path == "/api/cluster":
            self._handle_api_cluster()
            return
        if path == "/api/haproxy/stats":
            self._handle_api_haproxy_stats()
            return
        if path == "/api/containers":
            self._handle_api_containers()
            return
        if path.startswith("/api/logs/"):
            container = path[len("/api/logs/"):]
            self._handle_api_logs(container, query)
            return
        if path.startswith("/static/") or path.endswith((".js", ".css")):
            filename = os.path.basename(path)
            self._serve_static(filename)
            return

        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/api/geo/deploy":
            self._handle_proxy_post("geo-manager", self._config().geo_status_port, "/geo/deploy-now")
            return
        if path == "/api/cert/deploy":
            self._handle_proxy_post("cert-manager", self._config().cert_status_port, "/cert/deploy-now")
            return
        self.send_error(404)

    # -- helpers --

    def _config(self) -> Config:
        return self.server.config  # type: ignore[attr-defined]

    def _docker(self) -> DockerClient:
        return self.server.docker_client  # type: ignore[attr-defined]

    def _send_json_ok(self, data: Any) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, filename: str) -> None:
        safe = os.path.basename(filename)
        filepath = os.path.join(_STATIC_DIR, safe)
        if not os.path.isfile(filepath):
            self.send_error(404)
            return
        ext = os.path.splitext(safe)[1]
        content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(filepath, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # -- API: Cluster overview --

    def _fetch_node_status(self, node_ip: str) -> dict[str, Any]:
        """Fetch geo + cert status from a single node (called in thread pool)."""
        config = self._config()
        result: dict[str, Any] = {"ip": node_ip, "geo": None, "cert": None, "errors": []}

        # Geo status
        try:
            url = f"http://{node_ip}:{config.geo_status_port}/geo/status"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                result["geo"] = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            result["errors"].append(f"Geo: {exc}")

        # Cert status (local node via 127.0.0.1 to avoid hairpin)
        cert_host = node_ip
        if (result["geo"] and result["geo"].get("node_name") == config.node_name
                and result["geo"].get("node_prio") == config.node_prio):
            cert_host = "cert-manager"
        try:
            path = "/cert/status"
            if config.cert_cluster_key:
                path = f"/cert/status?cluster_key={config.cert_cluster_key}"
            url = f"http://{cert_host}:{config.cert_status_port}{path}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                result["cert"] = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            result["errors"].append(f"Cert: {exc}")

        return result

    def _handle_api_cluster(self) -> None:
        config = self._config()
        if not config.mesh_nodes:
            self._send_json_ok({"node": config.node_name, "prio": config.node_prio, "nodes": []})
            return

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(config.mesh_nodes)) as pool:
            futures = {pool.submit(self._fetch_node_status, ip): ip for ip in config.mesh_nodes}
            nodes = []
            for future in concurrent.futures.as_completed(futures):
                nodes.append(future.result())

        nodes.sort(key=lambda n: config.mesh_nodes.index(n["ip"]) if n["ip"] in config.mesh_nodes else 99)
        self._send_json_ok({
            "node": config.node_name,
            "prio": config.node_prio,
            "nodes": nodes,
        })

    # -- API: HAProxy stats --

    def _handle_api_haproxy_stats(self) -> None:
        config = self._config()
        try:
            url = f"http://haproxy:{8404}/metrics"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                raw = resp.read().decode("utf-8")
            stats = _parse_haproxy_prometheus(raw)
            self._send_json_ok(stats)
        except Exception as exc:
            self._send_json_ok({"error": str(exc), "backends": [], "frontends": []})

    # -- API: Docker containers --

    def _handle_api_containers(self) -> None:
        containers = self._docker().list_containers()
        result = []
        for c in containers:
            name = (c.get("Names") or ["/unknown"])[0].lstrip("/")
            state = c.get("State", "unknown")
            status = c.get("Status", "")
            health = ""
            if "Health" in status:
                health = status.split("(")[-1].rstrip(")") if "(" in status else status
            result.append({
                "name": name,
                "state": state,
                "status": status,
                "image": c.get("Image", ""),
                "created": c.get("Created", 0),
                "health": health,
            })
        result.sort(key=lambda c: c["name"])
        self._send_json_ok(result)

    # -- API: Docker logs --

    def _handle_api_logs(self, container: str, query: str) -> None:
        if not _ALLOWED_CONTAINER_RE.match(container):
            self.send_error(400, "Invalid container name")
            return
        lines = 200
        for param in query.split("&"):
            if param.startswith("lines="):
                try:
                    lines = min(int(param.split("=", 1)[1]), 1000)
                except ValueError:
                    pass
        logs = self._docker().get_container_logs(container, tail=lines)
        self._send_json_ok({"container": container, "lines": lines, "logs": logs})

    # -- API: Proxy POST --

    def _handle_proxy_post(self, host: str, port: int, path: str) -> None:
        import http.client
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10.0)
            conn.request("POST", path)
            resp = conn.getresponse()
            body = resp.read()
            status = resp.status
            conn.close()
        except Exception as exc:
            msg = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
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


def _parse_haproxy_prometheus(raw: str) -> dict[str, Any]:
    """Extract key metrics from HAProxy's Prometheus exporter output."""
    stats: dict[str, Any] = {
        "frontends": [],
        "backends": [],
        "total_connections": 0,
        "active_connections": 0,
        "request_rate": 0,
        "http_responses_total": {},
    }

    _frontends: dict[str, dict] = {}
    _backends: dict[str, dict] = {}

    for line in raw.splitlines():
        if line.startswith("#"):
            continue

        if "haproxy_frontend_current_sessions{" in line:
            name, val = _prom_extract(line, "proxy")
            if name:
                _frontends.setdefault(name, {"name": name})["current_sessions"] = int(float(val))

        elif "haproxy_frontend_http_requests_total{" in line:
            name, val = _prom_extract(line, "proxy")
            if name:
                _frontends.setdefault(name, {"name": name})["http_requests_total"] = int(float(val))

        elif "haproxy_frontend_bytes_in_total{" in line:
            name, val = _prom_extract(line, "proxy")
            if name:
                _frontends.setdefault(name, {"name": name})["bytes_in"] = int(float(val))

        elif "haproxy_frontend_bytes_out_total{" in line:
            name, val = _prom_extract(line, "proxy")
            if name:
                _frontends.setdefault(name, {"name": name})["bytes_out"] = int(float(val))

        elif "haproxy_frontend_request_errors_total{" in line:
            name, val = _prom_extract(line, "proxy")
            if name:
                _frontends.setdefault(name, {"name": name})["request_errors"] = int(float(val))

        elif "haproxy_backend_status{" in line:
            name, val = _prom_extract(line, "proxy")
            if name:
                _backends.setdefault(name, {"name": name})["status"] = int(float(val))

        elif "haproxy_backend_current_sessions{" in line:
            name, val = _prom_extract(line, "proxy")
            if name:
                _backends.setdefault(name, {"name": name})["current_sessions"] = int(float(val))

        elif "haproxy_backend_http_responses_total{" in line:
            name, val = _prom_extract(line, "proxy")
            code = _prom_label(line, "code")
            if name and code:
                _backends.setdefault(name, {"name": name}).setdefault("responses", {})[code] = int(float(val))

        elif "haproxy_backend_up{" in line:
            name, val = _prom_extract(line, "proxy")
            if name:
                _backends.setdefault(name, {"name": name})["up"] = int(float(val))

        elif "haproxy_process_current_connections " in line:
            val = line.split()[-1]
            stats["active_connections"] = int(float(val))

        elif "haproxy_process_connections_total " in line:
            val = line.split()[-1]
            stats["total_connections"] = int(float(val))

        elif "haproxy_process_requests_total " in line:
            val = line.split()[-1]
            stats["request_rate"] = int(float(val))

    for b in _backends.values():
        if "up" not in b and "status" in b:
            b["up"] = b["status"]

    stats["frontends"] = list(_frontends.values())
    stats["backends"] = list(_backends.values())
    return stats


def _prom_extract(line: str, label: str) -> tuple[str | None, str]:
    """Extract a label value and the metric value from a Prometheus line."""
    name = _prom_label(line, label)
    val = line.rsplit(" ", 1)[-1] if " " in line else "0"
    return name, val


def _prom_label(line: str, label: str) -> str | None:
    """Extract a specific label from a Prometheus metric line."""
    pattern = f'{label}="'
    idx = line.find(pattern)
    if idx < 0:
        return None
    start = idx + len(pattern)
    end = line.find('"', start)
    if end < 0:
        return None
    return line[start:end]


def main() -> None:
    config = Config.from_env()
    docker = DockerClient(config.docker_socket)

    server = HTTPServer(("0.0.0.0", config.dashboard_port), DashboardHandler)
    server.config = config  # type: ignore[attr-defined]
    server.docker_client = docker  # type: ignore[attr-defined]

    def _shutdown(_signum=None, _frame=None) -> None:
        logger.info("Shutting down dashboard")
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("Dashboard starting on port %s (node=%s, prio=%s)",
                config.dashboard_port, config.node_name, config.node_prio)
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
