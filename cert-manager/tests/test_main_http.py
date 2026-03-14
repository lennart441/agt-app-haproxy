import json
from io import BytesIO
from types import SimpleNamespace

from cert_manager.config import Config
from cert_manager.main import CertHandler
from cert_manager.state import set_state_from_pem


def _make_handler(path: str, config: Config) -> CertHandler:
    """
    Erzeuge einen CertHandler mit minimalen, aber echten Attributen, so dass
    die Handler-Methoden getestet werden können, ohne einen echten Socket zu öffnen.
    """

    class DummyServer:
        def __init__(self, cfg: Config) -> None:
            self.config = cfg

    server = DummyServer(config)

    # Simulierte Socket-ähnliche Verbindung mit makefile()
    class DummyConnection:
        def __init__(self) -> None:
            self._rfile = BytesIO()
            self._wfile = BytesIO()

        def makefile(self, mode: str, bufsize: int):
            if "r" in mode:
                return self._rfile
            return self._wfile

        def settimeout(self, timeout: float) -> None:  # pragma: no cover - trivial
            return

        def setsockopt(self, *args, **kwargs) -> None:  # pragma: no cover - trivial
            return

    conn = DummyConnection()

    # BaseHTTPRequestHandler erwartet request, client_address, server
    handler = CertHandler(conn, ("127.0.0.1", 0), server)
    handler.path = path
    handler.command = "GET"
    handler.requestline = f"GET {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    # Überschreibe wfile/rfile mit BytesIO, damit nichts auf echte Sockets geschrieben wird.
    handler.rfile = BytesIO()
    handler.wfile = BytesIO()
    return handler


def test_cert_handler_health(monkeypatch):
    cfg = Config.from_env()
    cfg.node_prio = 1
    cfg.cert_is_master = True
    handler = _make_handler("/health", cfg)
    handler.do_GET()
    body = handler.wfile.getvalue()
    assert b"OK" in body


def test_cert_handler_status_and_download(monkeypatch, tmp_path):
    cfg = Config.from_env()
    cfg.node_prio = 1
    cfg.cert_is_master = True

    # Zertifikat im State und auf Disk hinterlegen
    state = set_state_from_pem(b"dummy-pem")
    tmp_pem = tmp_path / "haproxy.pem"
    tmp_pem.write_bytes(b"dummy-pem")
    cfg.target_pem_path = str(tmp_pem)

    # /cert/status
    handler = _make_handler("/cert/status", cfg)
    handler.do_GET()
    raw = handler.wfile.getvalue()
    # HTTP-Header entfernen, nur den JSON-Body betrachten (nach der ersten Leerzeile).
    status_body = raw.split(b"\r\n\r\n", 1)[1]
    data = json.loads(status_body.decode("utf-8"))
    assert data["version"] == state.version
    assert data["cert_is_master"] is True

    # /cert/download mit korrekter Version
    handler = _make_handler(f"/cert/download?version={state.version}", cfg)
    handler.do_GET()
    download_raw = handler.wfile.getvalue()
    download_body = download_raw.split(b"\r\n\r\n", 1)[1]
    assert b"dummy-pem" in download_body

    # Datei entfernen -> FileNotFoundError-Zweig
    tmp_pem.unlink()
    handler = _make_handler(f"/cert/download?version={state.version}", cfg)
    handler.do_GET()

    # /cert/download mit unbekannter Version -> 404
    handler = _make_handler("/cert/download?version=unknown", cfg)
    handler.do_GET()
    error_raw = handler.wfile.getvalue()
    assert b"Unknown version" in error_raw


def test_cert_handler_download_no_state(monkeypatch):
    cfg = Config.from_env()
    cfg.node_prio = 1
    cfg.cert_is_master = True

    # State explizit leeren
    from cert_manager import state as state_module

    state_module._state = None  # type: ignore[attr-defined]

    handler = _make_handler("/cert/download", cfg)
    handler.do_GET()
    raw = handler.wfile.getvalue()
    assert b"No certificate active" in raw


def test_cert_handler_dashboard_removed(monkeypatch):
    """Dashboard route was moved to dedicated dashboard container; cert-manager returns 404."""
    cfg = Config.from_env()
    handler = _make_handler("/dashboard", cfg)
    handler.do_GET()
    raw = handler.wfile.getvalue()
    assert b"404" in raw


def test_cert_handler_geo_deploy_removed(monkeypatch):
    """Geo-deploy proxy was moved to dashboard container; cert-manager returns 404."""
    cfg = Config.from_env()
    handler = _make_handler("/geo/deploy-now", cfg)
    handler.command = "POST"
    handler.requestline = "POST /geo/deploy-now HTTP/1.1"
    handler.do_POST()
    raw = handler.wfile.getvalue()
    assert b"404" in raw


def test_cert_handler_deploy_now_master(monkeypatch, tmp_path):
    from cert_manager import main as main_mod

    cfg = Config.from_env()
    cfg.node_prio = 1
    cfg.cert_is_master = True

    handler = _make_handler("/cert/deploy-now", cfg)
    handler.send_response = lambda *args, **kwargs: None
    handler.send_header = lambda *args, **kwargs: None
    handler.end_headers = lambda *args, **kwargs: None

    called = {}

    def fake_run_leader_once(config: Config) -> bool:
        called["run"] = True
        return True

    monkeypatch.setattr(main_mod, "run_leader_once", fake_run_leader_once)
    handler.do_POST()
    assert called.get("run") is True


def test_cert_handler_deploy_now_forbidden_for_follower(monkeypatch):
    cfg = Config.from_env()
    cfg.node_prio = 2
    cfg.cert_is_master = False

    handler = _make_handler("/cert/deploy-now", cfg)
    # Replace send_error to capture calls
    errors = {}

    def fake_send_error(code, message=None):
        errors["code"] = code
        errors["message"] = message

    handler.send_error = fake_send_error  # type: ignore[assignment]
    handler.do_POST()
    assert errors.get("code") == 403


def test_cert_handler_status_forbidden_without_cluster_key():
    cfg = Config.from_env()
    cfg.cluster_key = "my-secret"
    cfg.cert_is_master = True
    handler = _make_handler("/cert/status", cfg)
    handler.do_GET()
    raw = handler.wfile.getvalue()
    assert b"403" in raw


def test_cert_handler_download_forbidden_without_cluster_key():
    cfg = Config.from_env()
    cfg.cluster_key = "my-secret"
    cfg.cert_is_master = True
    handler = _make_handler("/cert/download?version=v1", cfg)
    handler.do_GET()
    raw = handler.wfile.getvalue()
    assert b"403" in raw


def test_cert_handler_deploy_now_failure(monkeypatch):
    from cert_manager import main as main_mod

    cfg = Config.from_env()
    cfg.cert_is_master = True

    def fake_run_leader_once(config):
        return False

    monkeypatch.setattr(main_mod, "run_leader_once", fake_run_leader_once)
    handler = _make_handler("/cert/deploy-now", cfg)
    handler.do_POST()
    raw = handler.wfile.getvalue()
    assert b"500" in raw

