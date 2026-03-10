import json
from datetime import datetime, timezone

from cert_manager.config import Config
from cert_manager.follower import (
    _parse_iso8601,
    download_cert_from_master,
    get_master_status,
    run_follower_once,
)
from cert_manager.state import CertState, compute_version


def test_parse_iso8601_valid_and_invalid():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    s = now.isoformat()
    assert _parse_iso8601(s) == now
    assert _parse_iso8601("not-a-date") is None


def test_get_master_status_single_master(monkeypatch):
    cfg = Config.from_env()
    cfg.mesh_nodes = ["10.0.0.1"]
    payload = {
        "node_prio": 1,
        "node_name": "agt-1",
        "cert_is_master": True,
        "version": "v1",
        "validated_since": "2024-01-01T00:00:00+00:00",
    }
    body = json.dumps(payload).encode("utf-8")

    def fake_http_get(host, port, path, timeout=5.0):
        return 200, body

    monkeypatch.setattr("cert_manager.follower._http_get", fake_http_get)
    result = get_master_status(cfg)
    assert result is not None
    ip, state = result
    assert ip == "10.0.0.1"
    assert isinstance(state, CertState)
    assert state.version == "v1"


def test_get_master_status_no_master(monkeypatch):
    cfg = Config.from_env()
    cfg.mesh_nodes = ["10.0.0.1"]
    payload = {
        "node_prio": 1,
        "node_name": "agt-1",
        "cert_is_master": False,
        "version": "v1",
        "validated_since": "2024-01-01T00:00:00+00:00",
    }
    body = json.dumps(payload).encode("utf-8")

    def fake_http_get(host, port, path, timeout=5.0):
        return 200, body

    monkeypatch.setattr("cert_manager.follower._http_get", fake_http_get)
    assert get_master_status(cfg) is None


def test_get_master_status_http_status_not_200(monkeypatch):
    cfg = Config.from_env()
    cfg.mesh_nodes = ["10.0.0.1"]

    def http_get_500(host, port, path, timeout=5.0):
        return 500, b"error"

    monkeypatch.setattr("cert_manager.follower._http_get", http_get_500)
    # Status != 200 führt zu continue und am Ende None
    assert get_master_status(cfg) is None


def test_get_master_status_http_error_and_missing_fields(monkeypatch):
    cfg = Config.from_env()
    cfg.mesh_nodes = ["10.0.0.1", "10.0.0.2"]

    def raising_http_get(host, port, path, timeout=5.0):
        if host == "10.0.0.1":
            raise RuntimeError("network down")
        payload = {
            "node_prio": 1,
            "node_name": "agt-1",
            "cert_is_master": True,
            # version fehlt absichtlich
            "validated_since": "2024-01-01T00:00:00+00:00",
        }
        return 200, json.dumps(payload).encode("utf-8")

    monkeypatch.setattr("cert_manager.follower._http_get", raising_http_get)
    # Beide Antworten sind ungültig (Exception bzw. fehlende Version) -> kein Master
    assert get_master_status(cfg) is None


def test_get_master_status_invalid_json_and_date(monkeypatch):
    cfg = Config.from_env()
    cfg.mesh_nodes = ["10.0.0.1", "10.0.0.2"]
    bad_json_body = b"{not-json"
    bad_date_payload = {
        "node_prio": 1,
        "node_name": "agt-1",
        "cert_is_master": True,
        "version": "v1",
        "validated_since": "not-a-date",
    }
    good_payload = {
        "node_prio": 1,
        "node_name": "agt-1",
        "cert_is_master": True,
        "version": "v2",
        "validated_since": "2024-01-01T00:00:00+00:00",
    }

    def fake_http_get(host, port, path, timeout=5.0):
        if host == "10.0.0.1":
            return 200, bad_json_body
        if host == "10.0.0.2":
            return 200, json.dumps(bad_date_payload).encode("utf-8")
        return 500, b""

    monkeypatch.setattr("cert_manager.follower._http_get", fake_http_get)
    # Beide Antworten sind ungültig -> kein Master
    assert get_master_status(cfg) is None

    def fake_http_get2(host, port, path, timeout=5.0):
        if host == "10.0.0.1":
            return 200, json.dumps(good_payload).encode("utf-8")
        return 404, b""

    monkeypatch.setattr("cert_manager.follower._http_get", fake_http_get2)
    cfg.mesh_nodes = ["10.0.0.1"]
    result = get_master_status(cfg)
    assert result is not None


def test_download_cert_from_master_success_and_failures(monkeypatch):
    cfg = Config.from_env()
    body = b"pem-bytes"
    version = compute_version(body)

    # Erfolgspfad
    def ok_http_get(host, port, path, timeout=5.0):
        return 200, body

    monkeypatch.setattr("cert_manager.follower._http_get", ok_http_get)
    data = download_cert_from_master("10.0.0.1", cfg, version)
    assert data == body

    # HTTP-Fehler
    def bad_http_get(host, port, path, timeout=5.0):
        return 500, body

    monkeypatch.setattr("cert_manager.follower._http_get", bad_http_get)
    assert download_cert_from_master("10.0.0.1", cfg, version) is None

    # Hash-Mismatch
    def mismatch_http_get(host, port, path, timeout=5.0):
        return 200, b"other"

    monkeypatch.setattr("cert_manager.follower._http_get", mismatch_http_get)
    assert download_cert_from_master("10.0.0.1", cfg, version) is None

    # Ausnahme im HTTP-Client
    def raising_http_get(host, port, path, timeout=5.0):
        raise RuntimeError("network down")

    monkeypatch.setattr("cert_manager.follower._http_get", raising_http_get)
    assert download_cert_from_master("10.0.0.1", cfg, version) is None


def test_should_activate_master_and_zero_delay():
    from cert_manager.follower import should_activate

    now = datetime.now(timezone.utc)
    state = CertState(version="v", validated_since=now)

    cfg = Config.from_env()
    # Master selbst soll niemals als Follower aktivieren
    cfg.cert_is_master = True
    assert should_activate(cfg, state, now=now) is False

    # Follower mit Delay 0 aktiviert sofort
    cfg.cert_is_master = False
    cfg.node_prio = 2
    cfg.stage_delay_prio2_hours = 0
    assert should_activate(cfg, state, now=now) is True

    # now=None-Pfad: sollte ebenfalls funktionieren
    cfg.stage_delay_prio2_hours = 1
    past_state = CertState(version="v", validated_since=now.replace(year=now.year - 1))
    assert should_activate(cfg, past_state) is True


def test_run_follower_once_bootstrap_no_local_pem(monkeypatch, tmp_path):
    """Bootstrap: Keine lokale PEM → sofort vom Master holen (ohne Staged-Delay)."""
    target_pem = tmp_path / "haproxy.pem"
    assert not target_pem.exists()
    cfg = Config.from_env()
    cfg.cert_is_master = False
    cfg.mesh_nodes = ["10.0.0.1"]
    cfg.target_pem_path = str(target_pem)
    cfg.stage_delay_prio2_hours = 1
    cfg.node_prio = 2
    pem_bytes = b"-----BEGIN CERTIFICATE-----\ndummy\n-----END CERTIFICATE-----"
    version = compute_version(pem_bytes)
    status_body = json.dumps({
        "node_prio": 1,
        "node_name": "agt-1",
        "cert_is_master": True,
        "version": version,
        "validated_since": datetime.now(timezone.utc).isoformat(),
    }).encode("utf-8")

    def fake_http_get(host, port, path, timeout=5.0):
        if "status" in path:
            return 200, status_body
        if "download" in path:
            return 200, pem_bytes
        return 404, b""

    monkeypatch.setattr("cert_manager.follower._http_get", fake_http_get)
    ok = run_follower_once(cfg)
    assert ok is True
    assert target_pem.exists()
    assert target_pem.read_bytes() == pem_bytes


def test_run_follower_once_respects_delay_when_pem_exists(monkeypatch, tmp_path):
    """Wenn lokale PEM existiert: Staged-Delay einhalten, ohne Delay kein Update."""
    target_pem = tmp_path / "haproxy.pem"
    target_pem.write_bytes(b"existing-pem")
    cfg = Config.from_env()
    cfg.cert_is_master = False
    cfg.mesh_nodes = ["10.0.0.1"]
    cfg.target_pem_path = str(target_pem)
    cfg.stage_delay_prio2_hours = 1
    cfg.node_prio = 2
    # Master-State mit „gerade eben“ validiert → should_activate = False
    now = datetime.now(timezone.utc).isoformat()
    status_body = json.dumps({
        "node_prio": 1,
        "node_name": "agt-1",
        "cert_is_master": True,
        "version": "new-version",
        "validated_since": now,
    }).encode("utf-8")

    def fake_http_get(host, port, path, timeout=5.0):
        if "status" in path:
            return 200, status_body
        return 404, b""

    monkeypatch.setattr("cert_manager.follower._http_get", fake_http_get)
    ok = run_follower_once(cfg)
    assert ok is False
    assert target_pem.read_bytes() == b"existing-pem"

