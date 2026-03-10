from datetime import datetime, timezone, timedelta
from pathlib import Path

from cert_manager.config import Config
from cert_manager.leader import build_combined_pem, run_leader_once
from cert_manager.state import compute_version, get_state, set_state_from_pem
from cert_manager.follower import should_activate


def _make_tmp_files(tmp_path: Path) -> tuple[str, str]:
    fullchain = tmp_path / "fullchain.pem"
    privkey = tmp_path / "privkey.pem"
    fullchain.write_text(
        "-----BEGIN CERTIFICATE-----\nMIID...\n-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )
    privkey.write_text(
        "-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    return str(fullchain), str(privkey)


def test_build_combined_pem(tmp_path, monkeypatch):
    fullchain, privkey = _make_tmp_files(tmp_path)
    monkeypatch.setenv("CERT_SOURCE_FULLCHAIN", fullchain)
    monkeypatch.setenv("CERT_SOURCE_PRIVKEY", privkey)
    cfg = Config.from_env()

    pem = build_combined_pem(cfg)
    assert pem is not None
    text = pem.decode("utf-8")
    assert "BEGIN CERTIFICATE" in text
    assert "BEGIN PRIVATE KEY" in text


def test_run_leader_once_writes_target(tmp_path, monkeypatch):
    fullchain, privkey = _make_tmp_files(tmp_path)
    target = tmp_path / "haproxy.pem"
    monkeypatch.setenv("CERT_SOURCE_FULLCHAIN", fullchain)
    monkeypatch.setenv("CERT_SOURCE_PRIVKEY", privkey)
    monkeypatch.setenv("CERT_TARGET_PEM_PATH", str(target))

    cfg = Config.from_env()
    ok = run_leader_once(cfg)
    assert ok is True
    assert target.exists()
    pem_bytes = target.read_bytes()
    # State should be updated
    state = get_state()
    assert state is not None
    assert state.version == compute_version(pem_bytes)


def test_set_state_from_pem_and_should_activate():
    now = datetime.now(timezone.utc)
    past = now - timedelta(hours=2)
    pem = b"dummy pem"
    state = set_state_from_pem(pem)
    # Simulate validation 2h ago
    state.validated_since = past

    cfg = Config.from_env()
    cfg.node_prio = 2
    cfg.stage_delay_prio2_hours = 1
    assert should_activate(cfg, state, now=now) is True

