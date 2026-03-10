import os

from cert_manager.config import Config


def test_config_from_env_defaults(monkeypatch):
    monkeypatch.delenv("NODE_PRIO", raising=False)
    monkeypatch.delenv("MESH_NODES", raising=False)
    monkeypatch.delenv("CERT_STATUS_PORT", raising=False)
    monkeypatch.delenv("CERT_STAGE_DELAY_PRIO2_HOURS", raising=False)
    monkeypatch.delenv("CERT_STAGE_DELAY_PRIO3_HOURS", raising=False)
    monkeypatch.delenv("CERT_POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("CERT_IS_MASTER", raising=False)
    cfg = Config.from_env()
    assert cfg.node_name == os.environ.get("NODE_NAME", "agt-1")
    assert cfg.node_prio == 1
    assert cfg.cert_is_master is False
    assert cfg.mesh_nodes == []
    assert cfg.status_port == 8081
    assert cfg.stage_delay_prio2_hours == 1
    assert cfg.stage_delay_prio3_hours == 2
    assert cfg.poll_interval_seconds >= 30


def test_stage_delay_hours_for_prio():
    cfg = Config.from_env()
    cfg.stage_delay_prio2_hours = 5
    cfg.stage_delay_prio3_hours = 7
    assert cfg.stage_delay_hours_for_prio(1) == 0
    assert cfg.stage_delay_hours_for_prio(2) == 5
    assert cfg.stage_delay_hours_for_prio(3) == 7
    # Fallback for unknown priorities
    assert cfg.stage_delay_hours_for_prio(4) >= 7


def test_config_from_env_invalid_values(monkeypatch):
    # Ungültige Integer-Werte -> Fallback auf Defaults
    monkeypatch.setenv("CERT_STATUS_PORT", "70000")  # > 65535 -> Default
    monkeypatch.setenv("CERT_STAGE_DELAY_PRIO2_HOURS", "x")
    monkeypatch.setenv("CERT_STAGE_DELAY_PRIO3_HOURS", "y")
    # < 30 -> auf mindestens 30 kappen
    monkeypatch.setenv("CERT_POLL_INTERVAL_SECONDS", "5")
    cfg = Config.from_env()
    assert cfg.status_port == 8081
    assert cfg.stage_delay_prio2_hours == 1
    assert cfg.stage_delay_prio3_hours == 2
    assert cfg.poll_interval_seconds >= 30

