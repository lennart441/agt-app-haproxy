"""Tests for geo_manager.validation."""
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from geo_manager.config import Config
from geo_manager.validation import (
    DEFAULT_HAPROXY_CRT_PATH,
    ENV_HAPROXY_CRT_PATH_FOR_VALIDATION,
    PEER_LINE_1_TEMPLATE,
    PEER_LINE_2_TEMPLATE,
    PEER_LINE_3_TEMPLATE,
    _apply_template_replacements,
    _build_peer_lines,
    _get_processed_config_path,
    build_permissive_geo_map,
    count_geo_data_lines,
    validate_anchors,
    validate_size,
    validate_syntax,
    validate_syntax_with_config,
    persist_size,
    _lookup_country_for_ip,
    GEO_MAP_SIZE_FILE,
)


def test_build_peer_lines_local_agt1():
    config = Config.from_env()
    config.node_name = "agt-1"
    config.mesh_nodes = ["172.20.0.1", "172.20.0.2", "172.20.0.3"]
    l1, l2, l3 = _build_peer_lines(config)
    assert l1 == "   server agt-1"
    assert l2 == "   server agt-2 172.20.0.2:50000"
    assert l3 == "   server agt-3 172.20.0.3:50000"


def test_build_peer_lines_local_agt2():
    config = Config.from_env()
    config.node_name = "agt-2"
    config.mesh_nodes = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    l1, l2, l3 = _build_peer_lines(config)
    assert l1 == "   server agt-1 10.0.0.1:50000"
    assert l2 == "   server agt-2"
    assert l3 == "   server agt-3 10.0.0.3:50000"


def test_build_peer_lines_fewer_mesh_nodes_uses_defaults():
    """With only one mesh node, agt-2/agt-3 use default IPs (172.20.0.2, 172.20.0.3)."""
    config = Config.from_env()
    config.node_name = "agt-1"
    config.mesh_nodes = ["192.168.1.1"]
    l1, l2, l3 = _build_peer_lines(config)
    assert l1 == "   server agt-1"
    assert l2 == "   server agt-2 172.20.0.2:50000"
    assert l3 == "   server agt-3 172.20.0.3:50000"


def test_apply_template_replacements_mesh_ips():
    """__MESH_IP_*__ placeholders in backends are replaced with MESH_NODES IPs."""
    config = Config.from_env()
    config.node_name = "agt-1"
    config.mesh_nodes = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    content = "   server agt-1 __MESH_IP_1__:3102 check\n   server agt-2 __MESH_IP_2__:3102 check\n"
    result = _apply_template_replacements(content, config)
    assert "10.0.0.1:3102" in result
    assert "10.0.0.2:3102" in result


def test_apply_template_replacements_mesh_ips_defaults():
    """With fewer than 3 mesh nodes, remaining __MESH_IP_*__ use default IPs."""
    config = Config.from_env()
    config.node_name = "agt-1"
    config.mesh_nodes = ["10.0.0.1"]
    content = "__MESH_IP_1__ __MESH_IP_2__ __MESH_IP_3__"
    result = _apply_template_replacements(content, config)
    assert "10.0.0.1" in result
    assert "172.20.0.2" in result
    assert "172.20.0.3" in result


def test_get_processed_config_path_replaces_placeholders(tmp_path):
    cfg = tmp_path / "haproxy.cfg"
    cfg.write_text(
        "localpeer __NODE_NAME__\n"
        "acl too_many_conn sc1_conn_cur(st_global_conn) ge __CLUSTER_MAXCONN__\n"
        + PEER_LINE_1_TEMPLATE + "\n"
        + PEER_LINE_2_TEMPLATE + "\n"
        + PEER_LINE_3_TEMPLATE + "\n"
        "   server agt-1 __MESH_IP_1__:3102 check\n"
    )
    config = Config.from_env()
    config.node_name = "agt-2"
    config.cluster_maxconn = 300
    config.mesh_nodes = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    path = _get_processed_config_path(str(cfg), config)
    try:
        content = open(path).read()
        assert "localpeer agt-2" in content
        assert "ge 300" in content
        assert "   server agt-1 10.0.0.1:50000" in content
        assert "   server agt-2\n" in content or "   server agt-2" in content
        assert "   server agt-3 10.0.0.3:50000" in content
        assert "10.0.0.1:3102 check" in content
    finally:
        os.unlink(path)


def test_get_processed_config_path_replaces_crt_path_when_env_set(tmp_path, monkeypatch):
    """When HAPROXY_CRT_PATH_FOR_VALIDATION is set, bind line uses that path for haproxy -c."""
    monkeypatch.setenv(ENV_HAPROXY_CRT_PATH_FOR_VALIDATION, "/etc/ssl/haproxy-pem/haproxy.pem")
    cfg = tmp_path / "haproxy.cfg"
    cfg.write_text("bind :443 ssl crt " + DEFAULT_HAPROXY_CRT_PATH + "\n")
    config = Config.from_env()
    path = _get_processed_config_path(str(cfg), config)
    try:
        content = open(path).read()
        assert "/etc/ssl/haproxy-pem/haproxy.pem" in content
        assert DEFAULT_HAPROXY_CRT_PATH not in content
    finally:
        os.unlink(path)


def test_get_processed_config_path_directory(tmp_path):
    """When cfg_path is a directory, all .cfg files are processed and a temp directory is returned."""
    cfg_dir = tmp_path / "conf.d"
    cfg_dir.mkdir()
    (cfg_dir / "00-global.cfg").write_text("localpeer __NODE_NAME__\n")
    (cfg_dir / "10-peers.cfg").write_text(
        PEER_LINE_1_TEMPLATE + "\n"
        + PEER_LINE_2_TEMPLATE + "\n"
        + PEER_LINE_3_TEMPLATE + "\n"
    )
    (cfg_dir / "60-backends.cfg").write_text("   server agt-1 __MESH_IP_1__:3102 check\n")
    config = Config.from_env()
    config.node_name = "agt-2"
    config.mesh_nodes = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    result_path = _get_processed_config_path(str(cfg_dir), config)
    try:
        assert os.path.isdir(result_path)
        global_content = open(os.path.join(result_path, "00-global.cfg")).read()
        assert "localpeer agt-2" in global_content
        peers_content = open(os.path.join(result_path, "10-peers.cfg")).read()
        assert "   server agt-1 10.0.0.1:50000" in peers_content
        assert "   server agt-2\n" in peers_content
        backends_content = open(os.path.join(result_path, "60-backends.cfg")).read()
        assert "10.0.0.1:3102" in backends_content
    finally:
        shutil.rmtree(result_path)


def test_get_processed_config_path_directory_skips_non_cfg(tmp_path):
    """Non-.cfg files in conf.d directory are not processed."""
    cfg_dir = tmp_path / "conf.d"
    cfg_dir.mkdir()
    (cfg_dir / "00-global.cfg").write_text("global\n")
    (cfg_dir / "notes.txt").write_text("should be ignored\n")
    (cfg_dir / "backup.cfg.bak").write_text("should be ignored too\n")
    config = Config.from_env()
    result_path = _get_processed_config_path(str(cfg_dir), config)
    try:
        assert os.path.isdir(result_path)
        assert os.path.isfile(os.path.join(result_path, "00-global.cfg"))
        assert not os.path.exists(os.path.join(result_path, "notes.txt"))
        assert not os.path.exists(os.path.join(result_path, "backup.cfg.bak"))
    finally:
        shutil.rmtree(result_path)


def test_validate_syntax_with_config_missing_file():
    config = Config.from_env()
    assert validate_syntax_with_config("/nonexistent/haproxy.cfg", "/tmp", config) is False


def test_validate_syntax_with_config_success(tmp_path):
    cfg = tmp_path / "haproxy.cfg"
    cfg.write_text("global\n  daemon\n")
    config = Config.from_env()
    config.haproxy_cfg_path = str(cfg)
    config.map_dir = str(tmp_path)
    with patch("geo_manager.validation.subprocess.run") as m:
        m.return_value = MagicMock(returncode=0, stderr="")
        assert validate_syntax_with_config(str(cfg), str(tmp_path), config) is True


def test_validate_syntax_with_config_unlink_oserror_still_returns_result(tmp_path):
    """When temp file unlink raises OSError, result is still returned (finally block covered)."""
    cfg = tmp_path / "haproxy.cfg"
    cfg.write_text("global\n  daemon\n")
    config = Config.from_env()
    with patch("geo_manager.validation.subprocess.run") as m:
        m.return_value = MagicMock(returncode=0, stderr="")
        with patch("geo_manager.validation.os.unlink", side_effect=OSError):
            assert validate_syntax_with_config(str(cfg), str(tmp_path), config) is True


def test_validate_syntax_with_config_directory(tmp_path):
    """validate_syntax_with_config works with a conf.d directory."""
    cfg_dir = tmp_path / "conf.d"
    cfg_dir.mkdir()
    (cfg_dir / "00-global.cfg").write_text("global\n  daemon\n")
    config = Config.from_env()
    with patch("geo_manager.validation.subprocess.run") as m:
        m.return_value = MagicMock(returncode=0, stderr="")
        assert validate_syntax_with_config(str(cfg_dir), str(tmp_path), config) is True


def test_validate_syntax_with_config_directory_rmtree_oserror(tmp_path):
    """When temp directory rmtree raises OSError, result is still returned."""
    cfg_dir = tmp_path / "conf.d"
    cfg_dir.mkdir()
    (cfg_dir / "00-global.cfg").write_text("global\n  daemon\n")
    config = Config.from_env()
    with patch("geo_manager.validation.subprocess.run") as m:
        m.return_value = MagicMock(returncode=0, stderr="")
        with patch("geo_manager.validation.shutil.rmtree", side_effect=OSError):
            assert validate_syntax_with_config(str(cfg_dir), str(tmp_path), config) is True


def test_validate_syntax_config_missing():
    assert validate_syntax("/nonexistent/haproxy.cfg", "/tmp") is False


def test_validate_syntax_success(tmp_path):
    cfg = tmp_path / "haproxy.cfg"
    cfg.write_text("global\n  daemon\n")
    with patch("geo_manager.validation.subprocess.run") as m:
        m.return_value = MagicMock(returncode=0, stderr="")
        assert validate_syntax(str(cfg), str(tmp_path)) is True


def test_validate_syntax_directory(tmp_path):
    """validate_syntax accepts a directory path (haproxy -c -f <dir>)."""
    cfg_dir = tmp_path / "conf.d"
    cfg_dir.mkdir()
    (cfg_dir / "00-global.cfg").write_text("global\n  daemon\n")
    with patch("geo_manager.validation.subprocess.run") as m:
        m.return_value = MagicMock(returncode=0, stderr="")
        assert validate_syntax(str(cfg_dir), str(tmp_path)) is True


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


def test_count_geo_data_lines():
    assert count_geo_data_lines("") == 0
    assert count_geo_data_lines("# comment\n") == 0
    assert count_geo_data_lines("1.0.0.0/24\tDE\n") == 1
    assert count_geo_data_lines("1.0.0.0/24\tDE\n2.0.0.0/24\tAT\n") == 2
    assert count_geo_data_lines("# c\n1.0.0.0/24\tDE\n\n2.0.0.0/24\tAT\n") == 2
    assert count_geo_data_lines("line without tab\n") == 0


def test_build_permissive_geo_map():
    out = build_permissive_geo_map(frozenset({"DE", "AT"}))
    assert "0.0.0.0/0\t" in out
    assert "::/0\t" in out
    assert out.strip().endswith("DE") or out.strip().endswith("AT")
    empty = build_permissive_geo_map(frozenset())
    assert "0.0.0.0/0\tDE\n" in empty
    assert "::/0\tDE\n" in empty


def test_validate_anchors_skips_comments():
    content = "8.8.8.8/32\tDE\n"
    assert validate_anchors(content, ["# ignore", "8.8.8.8"]) is True


def test_persist_size(tmp_path):
    persist_size(str(tmp_path), 12345)
    assert (tmp_path / GEO_MAP_SIZE_FILE).read_text() == "12345"
