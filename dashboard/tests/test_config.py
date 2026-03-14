import os

from dashboard.config import Config


def test_config_from_env_defaults(monkeypatch):
    for key in ("NODE_NAME", "NODE_PRIO", "MESH_NODES", "GEO_STATUS_PORT",
                "CERT_STATUS_PORT", "CERT_CLUSTER_KEY", "STATS_USER",
                "STATS_PASSWORD", "DASHBOARD_PORT", "DOCKER_SOCKET"):
        monkeypatch.delenv(key, raising=False)
    cfg = Config.from_env()
    assert cfg.node_name == "agt-1"
    assert cfg.node_prio == 1
    assert cfg.mesh_nodes == []
    assert cfg.geo_status_port == 8080
    assert cfg.cert_status_port == 8081
    assert cfg.cert_cluster_key == ""
    assert cfg.stats_user == "admin"
    assert cfg.stats_password == ""
    assert cfg.dashboard_port == 8082
    assert cfg.docker_socket == "/var/run/docker.sock"


def test_config_from_env_custom(monkeypatch):
    monkeypatch.setenv("NODE_NAME", "agt-3")
    monkeypatch.setenv("NODE_PRIO", "3")
    monkeypatch.setenv("MESH_NODES", "10.0.0.1,10.0.0.2,10.0.0.3")
    monkeypatch.setenv("GEO_STATUS_PORT", "9080")
    monkeypatch.setenv("CERT_STATUS_PORT", "9081")
    monkeypatch.setenv("CERT_CLUSTER_KEY", "secret123")
    monkeypatch.setenv("STATS_USER", "haproxy")
    monkeypatch.setenv("STATS_PASSWORD", "s3cret")
    monkeypatch.setenv("DASHBOARD_PORT", "9082")
    monkeypatch.setenv("DOCKER_SOCKET", "/tmp/docker.sock")
    cfg = Config.from_env()
    assert cfg.node_name == "agt-3"
    assert cfg.node_prio == 3
    assert cfg.mesh_nodes == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    assert cfg.geo_status_port == 9080
    assert cfg.cert_status_port == 9081
    assert cfg.cert_cluster_key == "secret123"
    assert cfg.dashboard_port == 9082
    assert cfg.docker_socket == "/tmp/docker.sock"


def test_config_invalid_int_fallback(monkeypatch):
    monkeypatch.setenv("NODE_PRIO", "abc")
    monkeypatch.setenv("DASHBOARD_PORT", "notanumber")
    cfg = Config.from_env()
    assert cfg.node_prio == 1
    assert cfg.dashboard_port == 8082
