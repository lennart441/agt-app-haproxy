"""Tests for geo_manager.config."""
import os

import pytest

from geo_manager.config import Config, ALLOWED_COUNTRY_CODES


def test_allowed_country_codes():
    assert "DE" in ALLOWED_COUNTRY_CODES
    assert "AT" in ALLOWED_COUNTRY_CODES
    assert "US" not in ALLOWED_COUNTRY_CODES


def test_config_from_env_defaults():
    config = Config.from_env()
    assert config.node_name == "agt-1"
    assert config.node_prio == 1
    assert config.mesh_nodes == []
    assert config.anchor_ips == []
    assert config.geo_source_url == ""
    assert config.map_dir == "/usr/local/etc/haproxy/maps"
    assert "haproxy.cfg" in config.haproxy_cfg_path
    assert config.haproxy_socket == "/var/run/haproxy.sock"
    assert config.stage_delay_prio2_hours == 48
    assert config.stage_delay_prio3_hours == 96
    assert config.fetch_interval_hours == 24.0
    assert config.status_port == 8080
    assert config.size_deviation_threshold == 0.9
    assert config.build_nice_level == 10
    assert config.build_chunk_size == 5000
    assert config.build_sleep_after_chunk_ms == 50


def test_config_from_env_custom(monkeypatch):
    monkeypatch.setenv("NODE_NAME", "agt-2")
    monkeypatch.setenv("NODE_PRIO", "2")
    monkeypatch.setenv("MESH_NODES", "172.20.0.1, 172.20.0.2")
    monkeypatch.setenv("ANCHOR_IPS", "8.8.8.8, 1.1.1.1")
    monkeypatch.setenv("GEO_SOURCE_URL", "https://example.com/geo.csv")
    monkeypatch.setenv("STAGE_DELAY_PRIO2_HOURS", "24")
    monkeypatch.setenv("STAGE_DELAY_PRIO3_HOURS", "72")
    monkeypatch.setenv("GEO_STATUS_PORT", "9090")
    monkeypatch.setenv("SIZE_DEVIATION_THRESHOLD", "0.85")
    monkeypatch.setenv("BUILD_NICE_LEVEL", "15")
    monkeypatch.setenv("BUILD_CHUNK_SIZE", "2000")
    monkeypatch.setenv("BUILD_SLEEP_AFTER_CHUNK_MS", "100")

    config = Config.from_env()
    assert config.node_name == "agt-2"
    assert config.node_prio == 2
    assert config.mesh_nodes == ["172.20.0.1", "172.20.0.2"]
    assert config.anchor_ips == ["8.8.8.8", "1.1.1.1"]
    assert config.geo_source_url == "https://example.com/geo.csv"
    assert config.stage_delay_prio2_hours == 24
    assert config.stage_delay_prio3_hours == 72
    assert config.status_port == 9090
    assert config.size_deviation_threshold == 0.85
    assert config.build_nice_level == 15
    assert config.build_chunk_size == 2000
    assert config.build_sleep_after_chunk_ms == 100


def test_config_invalid_build_params_fallback(monkeypatch):
    monkeypatch.setenv("BUILD_NICE_LEVEL", "x")
    monkeypatch.setenv("BUILD_CHUNK_SIZE", "y")
    monkeypatch.setenv("BUILD_SLEEP_AFTER_CHUNK_MS", "z")
    config = Config.from_env()
    assert config.build_nice_level == 10
    assert config.build_chunk_size == 5000
    assert config.build_sleep_after_chunk_ms == 50


def test_config_invalid_prio_fallback(monkeypatch):
    monkeypatch.setenv("NODE_PRIO", "x")
    config = Config.from_env()
    assert config.node_prio == 1


def test_config_invalid_delays_fallback(monkeypatch):
    monkeypatch.setenv("STAGE_DELAY_PRIO2_HOURS", "x")
    monkeypatch.setenv("STAGE_DELAY_PRIO3_HOURS", "y")
    config = Config.from_env()
    assert config.stage_delay_prio2_hours == 48
    assert config.stage_delay_prio3_hours == 96


def test_config_invalid_fetch_interval_fallback(monkeypatch):
    monkeypatch.setenv("FETCH_INTERVAL_HOURS", "x")
    config = Config.from_env()
    assert config.fetch_interval_hours == 24.0


def test_config_invalid_threshold_fallback(monkeypatch):
    monkeypatch.setenv("SIZE_DEVIATION_THRESHOLD", "x")
    config = Config.from_env()
    assert config.size_deviation_threshold == 0.9


def test_config_invalid_status_port_fallback(monkeypatch):
    monkeypatch.setenv("GEO_STATUS_PORT", "x")
    config = Config.from_env()
    assert config.status_port == 8080


def test_stage_delay_hours_for_prio():
    config = Config.from_env()
    assert config.stage_delay_hours_for_prio(1) == 0
    assert config.stage_delay_hours_for_prio(2) == 48
    assert config.stage_delay_hours_for_prio(3) == 96
    assert config.stage_delay_hours_for_prio(5) >= 96


def test_am_i_master():
    config = Config.from_env()
    assert config.am_i_master() is True
    config.node_prio = 2
    assert config.am_i_master() is False
