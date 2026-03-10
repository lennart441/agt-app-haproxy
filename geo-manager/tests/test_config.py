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
    assert config.geo_source_ipv6_url is None
    assert config.geo_blocks_ipv6_url is None
    assert config.map_dir == "/usr/local/etc/haproxy/maps"
    assert "haproxy.cfg" in config.haproxy_cfg_path
    assert config.haproxy_socket == "/var/run/haproxy.sock"
    assert config.stage_delay_prio2_hours == 48
    assert config.stage_delay_prio3_hours == 96
    assert config.fetch_interval_hours == 24.0
    assert config.fetch_retries >= 1
    assert config.fetch_retry_delay_sec >= 1.0
    assert config.status_port == 8080
    assert config.size_deviation_threshold == 0.9
    assert config.build_nice_level == 10
    assert config.build_chunk_size == 5000
    assert config.build_sleep_after_chunk_ms == 50
    assert config.mail_enabled is False
    assert config.cluster_health_interval_hours >= 0.25
    assert config.cluster_health_timeout_sec >= 1.0


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


def test_config_mail_and_cluster_defaults(monkeypatch):
    monkeypatch.setenv("MAIL_ENABLED", "true")
    monkeypatch.setenv("MAIL_HOST", "smtp.example.com")
    monkeypatch.setenv("MAIL_PORT", "25")
    monkeypatch.setenv("MAIL_USE_TLS", "false")
    monkeypatch.setenv("MAIL_FROM", "geo@example.com")
    monkeypatch.setenv("MAIL_TO", "a@x.com,b@x.com")
    monkeypatch.setenv("CLUSTER_HEALTH_INTERVAL_HOURS", "24")
    monkeypatch.setenv("CLUSTER_HEALTH_TIMEOUT_SEC", "10")
    config = Config.from_env()
    assert config.mail_enabled is True
    assert config.mail_host == "smtp.example.com"
    assert config.mail_port == 25
    assert config.mail_use_tls is False
    assert config.mail_to == ["a@x.com", "b@x.com"]
    assert config.cluster_health_interval_hours == 24.0
    assert config.cluster_health_timeout_sec == 10.0


def test_config_geo_source_ipv6_url(monkeypatch):
    monkeypatch.setenv("GEO_SOURCE_IPV6_URL", "https://example.com/geo-ipv6.csv")
    config = Config.from_env()
    assert config.geo_source_ipv6_url == "https://example.com/geo-ipv6.csv"


def test_config_geo_blocks_ipv6_url(monkeypatch):
    monkeypatch.setenv("GEO_BLOCKS_IPV6_URL", "https://example.com/ipv6.csv")
    config = Config.from_env()
    assert config.geo_blocks_ipv6_url == "https://example.com/ipv6.csv"


def test_config_fetch_retries_and_delay(monkeypatch):
    monkeypatch.setenv("FETCH_RETRIES", "5")
    monkeypatch.setenv("FETCH_RETRY_DELAY_SEC", "120")
    config = Config.from_env()
    assert config.fetch_retries == 5
    assert config.fetch_retry_delay_sec == 120.0


def test_config_invalid_fetch_retries_and_delay_fallback(monkeypatch):
    monkeypatch.setenv("FETCH_RETRIES", "x")
    monkeypatch.setenv("FETCH_RETRY_DELAY_SEC", "y")
    config = Config.from_env()
    assert config.fetch_retries == 3
    assert config.fetch_retry_delay_sec == 60.0


def test_config_invalid_cluster_health_fallback(monkeypatch):
    monkeypatch.setenv("CLUSTER_HEALTH_INTERVAL_HOURS", "z")
    monkeypatch.setenv("CLUSTER_HEALTH_TIMEOUT_SEC", "w")
    config = Config.from_env()
    assert config.cluster_health_interval_hours == 168.0
    assert config.cluster_health_timeout_sec == 5.0


def test_config_invalid_mail_port_fallback(monkeypatch):
    monkeypatch.setenv("MAIL_PORT", "not_a_number")
    config = Config.from_env()
    assert config.mail_port == 587
