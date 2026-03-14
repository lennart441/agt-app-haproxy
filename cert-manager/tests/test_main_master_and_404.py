import signal

from cert_manager import main as main_module
from cert_manager.config import Config


class DummyServer:
    def __init__(self, *args, **kwargs):
        self.config = None

    def serve_forever(self):
        return


def test_main_starts_as_master(monkeypatch):
    # Konfiguration so patchen, dass am_i_master() True zurückgibt.
    cfg = Config.from_env()
    cfg.cert_is_master = True

    def fake_from_env():
        return cfg

    monkeypatch.setattr("cert_manager.main.Config.from_env", fake_from_env)
    monkeypatch.setattr("cert_manager.main.HTTPServer", DummyServer)

    called = {"leader": False}

    def fake_run_leader_once(conf):
        called["leader"] = True
        return True

    monkeypatch.setattr("cert_manager.main.run_leader_once", fake_run_leader_once)
    main_module.main()
    assert called["leader"] is True


def test_cert_handler_404(monkeypatch):
    # Handler direkt mit unbekanntem Pfad ausführen; es reicht, dass kein Fehler geworfen wird.
    from io import BytesIO
    from cert_manager.main import CertHandler

    cfg = Config.from_env()

    class DummyServer:
        def __init__(self, cfg):
            self.config = cfg

    class DummyConnection:
        def __init__(self):
            self._r = BytesIO()
            self._w = BytesIO()

        def makefile(self, mode, bufsize):
            return self._r if "r" in mode else self._w

        def settimeout(self, timeout):
            return

        def setsockopt(self, *args, **kwargs):
            return

    conn = DummyConnection()
    handler = CertHandler(conn, ("127.0.0.1", 0), DummyServer(cfg))
    handler.path = "/does-not-exist"
    handler.requestline = "GET /does-not-exist HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.command = "GET"
    handler.rfile = BytesIO()
    handler.wfile = BytesIO()
    handler.do_GET()
    # Wir prüfen nur, dass eine Antwort generiert wurde.
    assert handler.wfile.getvalue()


def test_main_shutdown_handler(monkeypatch):
    cfg = Config.from_env()
    cfg.cert_is_master = True

    monkeypatch.setattr("cert_manager.main.Config.from_env", lambda: cfg)

    shutdown_called = {}
    signal_handlers = {}

    class ShutdownServer:
        def __init__(self, *args, **kwargs):
            self.config = None

        def serve_forever(self):
            return

        def shutdown(self):
            shutdown_called["done"] = True

    monkeypatch.setattr("cert_manager.main.HTTPServer", ShutdownServer)

    def capture_signal(signum, handler):
        signal_handlers[signum] = handler

    monkeypatch.setattr(signal, "signal", capture_signal)
    monkeypatch.setattr("cert_manager.main.run_leader_once", lambda c: True)

    main_module.main()

    assert signal.SIGTERM in signal_handlers
    signal_handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert shutdown_called.get("done") is True

