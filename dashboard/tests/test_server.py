import json
import os
from io import BytesIO
from unittest.mock import MagicMock

from dashboard.config import Config
from dashboard.docker_client import DockerClient
from dashboard.server import DashboardHandler, _parse_haproxy_prometheus, _prom_label


def _make_config(**overrides) -> Config:
    cfg = Config.from_env()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_handler(path: str, config: Config, docker: DockerClient | None = None) -> DashboardHandler:
    class DummyServer:
        def __init__(self):
            self.config = config
            self.docker_client = docker or DockerClient("/tmp/nonexistent.sock")

    class DummyConnection:
        def __init__(self):
            self._rfile = BytesIO()
            self._wfile = BytesIO()

        def makefile(self, mode, bufsize):
            return self._rfile if "r" in mode else self._wfile

        def settimeout(self, timeout):
            return

        def setsockopt(self, *args, **kwargs):
            return

    conn = DummyConnection()
    server = DummyServer()

    handler = DashboardHandler(conn, ("127.0.0.1", 0), server)
    handler.path = path
    handler.command = "GET"
    handler.requestline = f"GET {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.rfile = BytesIO()
    handler.wfile = BytesIO()
    return handler


def _get_json(handler: DashboardHandler) -> dict:
    raw = handler.wfile.getvalue()
    body = raw.split(b"\r\n\r\n", 1)[1]
    return json.loads(body.decode("utf-8"))


# -- Health --

def test_health_endpoint():
    cfg = _make_config()
    handler = _make_handler("/health", cfg)
    handler.do_GET()
    data = _get_json(handler)
    assert data["status"] == "ok"


# -- Static file serving --

def test_serve_index_html():
    cfg = _make_config()
    handler = _make_handler("/", cfg)
    handler.do_GET()
    raw = handler.wfile.getvalue()
    assert b"HA-Cluster Dashboard" in raw
    assert b"text/html" in raw


def test_serve_css():
    cfg = _make_config()
    handler = _make_handler("/static/style.css", cfg)
    handler.do_GET()
    raw = handler.wfile.getvalue()
    assert b"text/css" in raw


def test_serve_js():
    cfg = _make_config()
    handler = _make_handler("/static/app.js", cfg)
    handler.do_GET()
    raw = handler.wfile.getvalue()
    assert b"application/javascript" in raw


def test_serve_404():
    cfg = _make_config()
    handler = _make_handler("/nonexistent", cfg)
    handler.do_GET()
    raw = handler.wfile.getvalue()
    assert b"404" in raw


# -- API: Cluster (no mesh nodes) --

def test_api_cluster_no_mesh():
    cfg = _make_config(mesh_nodes=[])
    handler = _make_handler("/api/cluster", cfg)
    handler.do_GET()
    data = _get_json(handler)
    assert data["nodes"] == []
    assert data["node"] == cfg.node_name


# -- API: Containers (no docker socket) --

def test_api_containers_no_socket():
    cfg = _make_config()
    handler = _make_handler("/api/containers", cfg)
    handler.do_GET()
    data = _get_json(handler)
    assert isinstance(data, list)
    assert data == []


# -- API: Logs --

def test_api_logs_invalid_container():
    cfg = _make_config()
    handler = _make_handler("/api/logs/../../etc/passwd", cfg)
    handler.do_GET()
    raw = handler.wfile.getvalue()
    assert b"400" in raw


def test_api_logs_valid_container_no_socket():
    cfg = _make_config()
    handler = _make_handler("/api/logs/haproxy_gateway?lines=50", cfg)
    handler.do_GET()
    data = _get_json(handler)
    assert data["container"] == "haproxy_gateway"
    assert data["lines"] == 50
    assert "Error:" in data["logs"]


# -- API: POST 404 --

def test_post_404():
    cfg = _make_config()
    handler = _make_handler("/api/nonexistent", cfg)
    handler.command = "POST"
    handler.requestline = "POST /api/nonexistent HTTP/1.1"
    handler.do_POST()
    raw = handler.wfile.getvalue()
    assert b"404" in raw


# -- HAProxy Prometheus parser --

def test_parse_haproxy_prometheus_empty():
    result = _parse_haproxy_prometheus("")
    assert result["frontends"] == []
    assert result["backends"] == []
    assert result["total_connections"] == 0


def test_parse_haproxy_prometheus_sample():
    sample = """# HELP haproxy_process_current_connections Current connections
haproxy_process_current_connections 42
haproxy_process_connections_total 12345
haproxy_process_requests_total 98765
haproxy_frontend_current_sessions{proxy="https-in"} 10
haproxy_frontend_http_requests_total{proxy="https-in"} 5000
haproxy_frontend_request_errors_total{proxy="https-in"} 3
haproxy_backend_up{proxy="api_backend"} 1
haproxy_backend_current_sessions{proxy="api_backend"} 5
haproxy_backend_http_responses_total{proxy="api_backend",code="2xx"} 4000
haproxy_backend_http_responses_total{proxy="api_backend",code="5xx"} 10
"""
    result = _parse_haproxy_prometheus(sample)
    assert result["active_connections"] == 42
    assert result["total_connections"] == 12345
    assert result["request_rate"] == 98765
    assert len(result["frontends"]) == 1
    assert result["frontends"][0]["name"] == "https-in"
    assert result["frontends"][0]["current_sessions"] == 10
    assert result["frontends"][0]["request_errors"] == 3
    assert len(result["backends"]) == 1
    assert result["backends"][0]["name"] == "api_backend"
    assert result["backends"][0]["up"] == 1
    assert result["backends"][0]["responses"]["2xx"] == 4000
    assert result["backends"][0]["responses"]["5xx"] == 10


def test_parse_haproxy_prometheus_status_fallback():
    """haproxy_backend_status (without haproxy_backend_up) must populate 'up'."""
    sample = (
        'haproxy_backend_status{proxy="web"} 1\n'
        'haproxy_backend_current_sessions{proxy="web"} 3\n'
        'haproxy_backend_status{proxy="down_be"} 0\n'
    )
    result = _parse_haproxy_prometheus(sample)
    backends = {b["name"]: b for b in result["backends"]}
    assert backends["web"]["status"] == 1
    assert backends["web"]["up"] == 1
    assert backends["down_be"]["status"] == 0
    assert backends["down_be"]["up"] == 0


def test_parse_haproxy_prometheus_up_takes_precedence():
    """When both haproxy_backend_up and haproxy_backend_status exist, 'up' keeps its value."""
    sample = (
        'haproxy_backend_status{proxy="be1"} 1\n'
        'haproxy_backend_up{proxy="be1"} 0\n'
    )
    result = _parse_haproxy_prometheus(sample)
    be = result["backends"][0]
    assert be["status"] == 1
    assert be["up"] == 0


def test_prom_label_extraction():
    line = 'haproxy_backend_http_responses_total{proxy="api",code="2xx"} 100'
    assert _prom_label(line, "proxy") == "api"
    assert _prom_label(line, "code") == "2xx"
    assert _prom_label(line, "missing") is None
