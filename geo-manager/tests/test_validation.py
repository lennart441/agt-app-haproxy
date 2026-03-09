"""Tests for geo_manager.validation."""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from geo_manager.validation import (
    validate_syntax,
    validate_size,
    validate_anchors,
    persist_size,
    _lookup_country_for_ip,
    GEO_MAP_SIZE_FILE,
)


def test_validate_syntax_config_missing():
    assert validate_syntax("/nonexistent/haproxy.cfg", "/tmp") is False


def test_validate_syntax_success(tmp_path):
    cfg = tmp_path / "haproxy.cfg"
    cfg.write_text("global\n  daemon\n")
    with patch("geo_manager.validation.subprocess.run") as m:
        m.return_value = MagicMock(returncode=0, stderr="")
        assert validate_syntax(str(cfg), str(tmp_path)) is True


def test_validate_syntax_failure(tmp_path):
    cfg = tmp_path / "haproxy.cfg"
    cfg.write_text("global\n  daemon\n")
    with patch("geo_manager.validation.subprocess.run") as m:
        m.return_value = MagicMock(returncode=1, stderr="error")
        assert validate_syntax(str(cfg), str(tmp_path)) is False


def test_validate_syntax_haproxy_not_found(tmp_path):
    cfg = tmp_path / "haproxy.cfg"
    cfg.write_text("x")
    with patch("subprocess.run") as m:
        m.side_effect = FileNotFoundError
        assert validate_syntax(str(cfg), str(tmp_path), haproxy_bin="nonexistent") is False


def test_validate_syntax_timeout(tmp_path):
    cfg = tmp_path / "haproxy.cfg"
    cfg.write_text("x")
    with patch("subprocess.run") as m:
        import subprocess
        m.side_effect = subprocess.TimeoutExpired("haproxy", 30)
        assert validate_syntax(str(cfg), str(tmp_path)) is False


def test_validate_size_no_previous(tmp_path):
    assert validate_size("1.0.0.0/24\tDE\n", str(tmp_path), 0.9) is True


def test_validate_size_pass(tmp_path):
    (tmp_path / GEO_MAP_SIZE_FILE).write_text("100")
    assert validate_size("x" * 95, str(tmp_path), 0.9) is True  # 95/100 >= 0.9


def test_validate_size_fail(tmp_path):
    (tmp_path / GEO_MAP_SIZE_FILE).write_text("1000")
    assert validate_size("x" * 50, str(tmp_path), 0.9) is False  # 50/1000 < 0.9


def test_validate_size_invalid_size_file(tmp_path):
    (tmp_path / GEO_MAP_SIZE_FILE).write_text("not a number")
    assert validate_size("content", str(tmp_path), 0.9) is True  # fallback accept


def test_validate_size_zero_old(tmp_path):
    (tmp_path / GEO_MAP_SIZE_FILE).write_text("0")
    assert validate_size("content", str(tmp_path), 0.9) is True


def test_lookup_country_for_ip():
    content = "10.0.0.0/8\tDE\n192.168.1.0/24\tAT\n"
    assert _lookup_country_for_ip(content, "10.1.2.3") == "DE"
    assert _lookup_country_for_ip(content, "192.168.1.1") == "AT"
    assert _lookup_country_for_ip(content, "8.8.8.8") is None


def test_lookup_country_for_ip_longest_prefix():
    content = "10.0.0.0/8\tDE\n10.0.0.0/24\tAT\n"
    assert _lookup_country_for_ip(content, "10.0.0.1") == "AT"


def test_lookup_country_for_ip_line_without_tab_skipped():
    content = "10.0.0.0/8\tDE\nsingle_column\n192.168.0.0/24\tAT\n"
    assert _lookup_country_for_ip(content, "192.168.0.1") == "AT"


def test_lookup_country_for_ip_invalid_network_skipped():
    content = "not-a-cidr\tDE\n10.0.0.0/8\tAT\n"
    assert _lookup_country_for_ip(content, "10.0.0.1") == "AT"


def test_lookup_country_for_ip_skips_comments():
    content = "# comment\n10.0.0.0/8\tDE\n"
    assert _lookup_country_for_ip(content, "10.0.0.1") == "DE"


def test_validate_anchors_all_allowed():
    content = "8.8.8.8/32\tDE\n1.1.1.1/32\tAT\n"
    assert validate_anchors(content, ["8.8.8.8", "1.1.1.1"]) is True


def test_validate_anchors_one_blocked():
    content = "8.8.8.8/32\tUS\n"
    assert validate_anchors(content, ["8.8.8.8"]) is False


def test_validate_anchors_missing_ip():
    content = "10.0.0.0/8\tDE\n"
    assert validate_anchors(content, ["8.8.8.8"]) is False


def test_validate_anchors_empty_list():
    assert validate_anchors("x\ty\n", []) is True


def test_validate_anchors_skips_comments():
    content = "8.8.8.8/32\tDE\n"
    assert validate_anchors(content, ["# ignore", "8.8.8.8"]) is True


def test_persist_size(tmp_path):
    persist_size(str(tmp_path), 12345)
    assert (tmp_path / GEO_MAP_SIZE_FILE).read_text() == "12345"
