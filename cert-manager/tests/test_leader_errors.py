from pathlib import Path

from cert_manager.config import Config
from cert_manager.leader import _read_file, build_combined_pem, run_leader_once


def test_read_file_not_found_and_empty(tmp_path, caplog):
    missing = tmp_path / "missing.pem"
    assert _read_file(str(missing)) is None

    empty = tmp_path / "empty.pem"
    empty.write_bytes(b"")
    assert _read_file(str(empty)) is None

    # OSError-Zweig
    def raising_open(path, mode):
        raise OSError("permission denied")

    import builtins

    orig_open = builtins.open
    builtins.open = raising_open
    try:
        assert _read_file(str(tmp_path / "any.pem")) is None
    finally:
        builtins.open = orig_open


def test_build_combined_pem_missing_env(monkeypatch, caplog):
    # Keine Pfade gesetzt -> None
    monkeypatch.delenv("CERT_SOURCE_FULLCHAIN", raising=False)
    monkeypatch.delenv("CERT_SOURCE_PRIVKEY", raising=False)
    cfg = Config.from_env()
    assert build_combined_pem(cfg) is None

    # run_leader_once sollte in diesem Fall False zurückgeben
    assert run_leader_once(cfg) is False


def test_build_combined_pem_invalid_markers(tmp_path, monkeypatch):
    fullchain = tmp_path / "fullchain.pem"
    privkey = tmp_path / "privkey.pem"
    # Ungültige Inhalte ohne BEGIN-Zeilen
    fullchain.write_text("no cert here", encoding="utf-8")
    privkey.write_text("no key here", encoding="utf-8")
    monkeypatch.setenv("CERT_SOURCE_FULLCHAIN", str(fullchain))
    monkeypatch.setenv("CERT_SOURCE_PRIVKEY", str(privkey))
    cfg = Config.from_env()
    assert build_combined_pem(cfg) is None

    # Nur Fullchain ungültig
    fullchain.write_text(
        "-----BEGIN CERTIFICATE-----\nMIID...\n-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )
    privkey.write_text("no key here", encoding="utf-8")
    assert build_combined_pem(cfg) is None

    # Nur Privkey ungültig
    fullchain.write_text("no cert here", encoding="utf-8")
    privkey.write_text(
        "-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    assert build_combined_pem(cfg) is None

    # Pfad gesetzt, aber Datei fehlt -> _read_file gibt None zurück und
    # build_combined_pem bricht aufgrund des None-Ergebnisses ab.
    missing = tmp_path / "does-not-exist.pem"
    monkeypatch.setenv("CERT_SOURCE_FULLCHAIN", str(missing))
    monkeypatch.setenv("CERT_SOURCE_PRIVKEY", str(privkey))
    cfg = Config.from_env()
    assert build_combined_pem(cfg) is None

