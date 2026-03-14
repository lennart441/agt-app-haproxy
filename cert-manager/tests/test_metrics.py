from io import BytesIO

from cert_manager.config import Config
from cert_manager.main import CertHandler
from cert_manager.metrics import (
    inc_deploy_failure,
    inc_deploy_success,
    inc_follower_sync_failure,
    inc_follower_sync_success,
    reset_for_tests,
    to_prometheus,
)
from cert_manager.state import CertState, set_state_from_pem


def _make_handler(path: str, config: Config) -> CertHandler:
    class DummyServer:
        def __init__(self, cfg: Config) -> None:
            self.config = cfg

    server = DummyServer(config)

    class DummyConnection:
        def __init__(self) -> None:
            self._rfile = BytesIO()
            self._wfile = BytesIO()

        def makefile(self, mode: str, bufsize: int):
            if "r" in mode:
                return self._rfile
            return self._wfile

        def settimeout(self, timeout: float) -> None:
            return

        def setsockopt(self, *args, **kwargs) -> None:
            return

    conn = DummyConnection()
    handler = CertHandler(conn, ("127.0.0.1", 0), server)
    handler.path = path
    handler.command = "GET"
    handler.requestline = f"GET {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.rfile = BytesIO()
    handler.wfile = BytesIO()
    return handler


def test_to_prometheus_no_state():
    reset_for_tests()
    cfg = Config.from_env()
    cfg.node_prio = 1
    cfg.cert_is_master = True
    text = to_prometheus(cfg, None)
    assert "cert_node_prio 1" in text
    assert "cert_is_master 1" in text
    assert "cert_validated_since_timestamp_seconds 0" in text
    assert 'cert_deploy_total{outcome="success"} 0' in text
    assert 'cert_deploy_total{outcome="failure"} 0' in text
    assert 'cert_follower_sync_total{outcome="success"} 0' in text
    assert 'cert_follower_sync_total{outcome="failure"} 0' in text


def test_to_prometheus_with_state():
    reset_for_tests()
    cfg = Config.from_env()
    cfg.node_prio = 2
    cfg.cert_is_master = False
    state = set_state_from_pem(b"test-pem-data")
    text = to_prometheus(cfg, state)
    assert "cert_node_prio 2" in text
    assert "cert_is_master 0" in text
    assert "cert_validated_since_timestamp_seconds" in text
    ts_line = [l for l in text.splitlines() if "cert_validated_since_timestamp_seconds" in l][0]
    ts_val = int(ts_line.split()[-1])
    assert ts_val > 0


def test_counters():
    reset_for_tests()
    cfg = Config.from_env()
    cfg.cert_is_master = True

    inc_deploy_success()
    inc_deploy_success()
    inc_deploy_failure()
    inc_follower_sync_success()
    inc_follower_sync_failure()
    inc_follower_sync_failure()
    inc_follower_sync_failure()

    text = to_prometheus(cfg, None)
    assert 'cert_deploy_total{outcome="success"} 2' in text
    assert 'cert_deploy_total{outcome="failure"} 1' in text
    assert 'cert_follower_sync_total{outcome="success"} 1' in text
    assert 'cert_follower_sync_total{outcome="failure"} 3' in text


def test_reset_for_tests():
    inc_deploy_success()
    inc_deploy_failure()
    inc_follower_sync_success()
    inc_follower_sync_failure()
    reset_for_tests()
    cfg = Config.from_env()
    text = to_prometheus(cfg, None)
    assert 'cert_deploy_total{outcome="success"} 0' in text
    assert 'cert_deploy_total{outcome="failure"} 0' in text
    assert 'cert_follower_sync_total{outcome="success"} 0' in text
    assert 'cert_follower_sync_total{outcome="failure"} 0' in text


def test_metrics_endpoint():
    reset_for_tests()
    cfg = Config.from_env()
    cfg.node_prio = 1
    cfg.cert_is_master = True
    set_state_from_pem(b"metrics-endpoint-pem")

    handler = _make_handler("/metrics", cfg)
    handler.do_GET()
    raw = handler.wfile.getvalue()
    body = raw.split(b"\r\n\r\n", 1)[1]
    text = body.decode("utf-8")
    assert "cert_node_prio 1" in text
    assert "cert_is_master 1" in text
    assert "cert_deploy_total" in text


def test_leader_increments_deploy_metrics(monkeypatch, tmp_path):
    reset_for_tests()
    from cert_manager.leader import run_leader_once

    cfg = Config.from_env()
    cfg.cert_is_master = True
    cfg.source_fullchain = str(tmp_path / "fullchain.pem")
    cfg.source_privkey = str(tmp_path / "privkey.pem")
    cfg.target_pem_path = str(tmp_path / "haproxy.pem")

    # Missing source files -> failure
    result = run_leader_once(cfg)
    assert result is False
    text = to_prometheus(cfg, None)
    assert 'cert_deploy_total{outcome="failure"} 1' in text
    assert 'cert_deploy_total{outcome="success"} 0' in text

    # Valid source files -> success
    (tmp_path / "fullchain.pem").write_bytes(
        b"-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n"
    )
    (tmp_path / "privkey.pem").write_bytes(
        b"-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n"
    )
    result = run_leader_once(cfg)
    assert result is True
    text = to_prometheus(cfg, None)
    assert 'cert_deploy_total{outcome="success"} 1' in text
    assert 'cert_deploy_total{outcome="failure"} 1' in text


def test_follower_increments_sync_metrics(monkeypatch, tmp_path):
    reset_for_tests()
    from cert_manager import follower as follower_mod

    cfg = Config.from_env()
    cfg.node_prio = 2
    cfg.cert_is_master = False
    cfg.mesh_nodes = ["10.0.0.1"]
    cfg.target_pem_path = str(tmp_path / "haproxy.pem")

    # No master found -> sync failure
    monkeypatch.setattr(follower_mod, "get_master_status", lambda c: None)
    result = follower_mod.run_follower_once(cfg)
    assert result is False
    text = to_prometheus(cfg, None)
    assert 'cert_follower_sync_total{outcome="failure"} 1' in text

    # Download fails -> sync failure
    from datetime import datetime, timezone
    master_state = CertState(version="abc123", validated_since=datetime.now(timezone.utc))
    monkeypatch.setattr(
        follower_mod, "get_master_status", lambda c: ("10.0.0.1", master_state)
    )
    monkeypatch.setattr(
        follower_mod, "download_cert_from_master", lambda ip, c, v: None
    )
    result = follower_mod.run_follower_once(cfg)
    assert result is False
    text = to_prometheus(cfg, None)
    assert 'cert_follower_sync_total{outcome="failure"} 2' in text

    # Successful sync
    pem_bytes = b"synced-pem"
    monkeypatch.setattr(
        follower_mod, "download_cert_from_master", lambda ip, c, v: pem_bytes
    )
    result = follower_mod.run_follower_once(cfg)
    assert result is True
    text = to_prometheus(cfg, None)
    assert 'cert_follower_sync_total{outcome="success"} 1' in text
