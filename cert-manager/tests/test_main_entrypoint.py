from cert_manager import __main__ as main_module


def test_main_entrypoint_runs(monkeypatch):
    # Verhindere, dass ein echter Server startet, indem HTTPServer.serve_forever gepatcht wird.
    class DummyServer:
        def __init__(self, *args, **kwargs):
            self.config = None

        def serve_forever(self):
            # Einmal aufgerufen, dann sofort zurückkehren
            return

    monkeypatch.setattr("cert_manager.main.HTTPServer", DummyServer)
    # main_module.main() sollte jetzt ohne Fehler durchlaufen.
    main_module.main()

