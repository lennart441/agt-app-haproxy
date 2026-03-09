"""Tests for geo_manager.cluster_health."""
from collections import deque
from unittest.mock import patch

import pytest

from geo_manager.cluster_health import (
    ClusterHealthState,
    NodeProbeResult,
    get_cluster_health_state,
    get_metrics_prometheus,
    probe_node,
    run_cluster_probe,
    set_cluster_health_state,
    update_and_get_json,
)


def test_node_probe_result_reachable():
    r = NodeProbeResult(node_ip="1.2.3.4", at="2026-01-01T12:00:00Z", reachable=True, latency_ms=5.0)
    assert r.reachable is True
    assert r.latency_ms == 5.0


def test_cluster_health_state_update_and_to_json_dict():
    state = ClusterHealthState()
    results = [
        NodeProbeResult("172.20.0.1", "2026-01-01T12:00:00Z", True, 2.5),
        NodeProbeResult("172.20.0.2", "2026-01-01T12:00:00Z", False, None, error="timeout"),
    ]
    state.update(results)
    d = state.to_json_dict()
    assert "last_probe_at" in d
    assert len(d["nodes"]) == 2
    assert d["nodes"][0]["node_ip"] == "172.20.0.1"
    assert d["nodes"][0]["reachable"] is True
    assert d["nodes"][0]["latency_ms"] == 2.5
    assert d["nodes"][1]["reachable"] is False
    assert "offline_summary" in d


def test_cluster_health_state_to_prometheus():
    state = ClusterHealthState()
    results = [
        NodeProbeResult("172.20.0.1", "2026-01-01T12:00:00Z", True, 3.0),
    ]
    state.update(results)
    out = state.to_prometheus()
    assert "geo_cluster_node_reachable" in out
    assert "172.20.0.1" in out
    assert "geo_cluster_node_latency_ms" in out
    assert "3.0" in out


def test_cluster_health_state_to_prometheus_empty():
    state = ClusterHealthState()
    out = state.to_prometheus()
    assert out == ""


def test_get_set_cluster_health_state():
    state = ClusterHealthState()
    set_cluster_health_state(state)
    assert get_cluster_health_state() is state


@patch("geo_manager.cluster_health.get_cluster_health_state")
def test_get_metrics_prometheus_no_state(mock_get):
    mock_get.return_value = None
    assert get_metrics_prometheus() == ""


def test_get_metrics_prometheus_with_state():
    state = ClusterHealthState()
    state.update([NodeProbeResult("1.2.3.4", "2026-01-01T12:00:00Z", True, 2.0)])
    set_cluster_health_state(state)
    out = get_metrics_prometheus()
    assert "geo_cluster_node_reachable" in out
    assert "1.2.3.4" in out


@patch("geo_manager.cluster_health.urllib.request.urlopen")
def test_probe_node_ok(mock_urlopen):
    mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"node_prio":1}'
    r = probe_node("127.0.0.1", 8080, 2.0)
    assert r.reachable is True
    assert r.latency_ms is not None
    assert r.node_ip == "127.0.0.1"


@patch("geo_manager.cluster_health.urllib.request.urlopen")
def test_probe_node_fail(mock_urlopen):
    mock_urlopen.side_effect = TimeoutError("timeout")
    r = probe_node("127.0.0.1", 8080, 2.0)
    assert r.reachable is False
    assert r.error is not None


def test_run_cluster_probe_empty_list():
    assert run_cluster_probe([], 8080, 5.0) == []


@patch("geo_manager.cluster_health.probe_node")
def test_run_cluster_probe_calls_probe_node(mock_probe):
    mock_probe.return_value = NodeProbeResult("1.2.3.4", "2026-01-01T12:00:00Z", True, 1.0)
    results = run_cluster_probe(["1.2.3.4"], 8080, 5.0)
    assert len(results) == 1
    mock_probe.assert_called_once_with("1.2.3.4", 8080, 5.0)


@patch("geo_manager.cluster_health.probe_node")
def test_run_cluster_probe_skips_blank_nodes(mock_probe):
    mock_probe.return_value = NodeProbeResult("1.2.3.4", "2026-01-01T12:00:00Z", True, 1.0)
    results = run_cluster_probe(["", "  ", "1.2.3.4"], 8080, 5.0)
    assert len(results) == 1
    mock_probe.assert_called_once_with("1.2.3.4", 8080, 5.0)


@patch("geo_manager.cluster_health.run_cluster_probe")
def test_update_and_get_json(mock_probe):
    mock_probe.return_value = [
        NodeProbeResult("172.20.0.1", "2026-01-01T12:00:00Z", True, 2.0),
    ]
    import json
    out = update_and_get_json(["172.20.0.1"], 8080, 5.0)
    data = json.loads(out)
    assert "nodes" in data
    assert len(data["nodes"]) == 1
    mock_probe.assert_called_once()


@patch("geo_manager.cluster_health.get_cluster_health_state")
@patch("geo_manager.cluster_health.run_cluster_probe")
def test_update_and_get_json_creates_state_when_none(mock_probe, mock_get):
    mock_get.return_value = None
    mock_probe.return_value = [
        NodeProbeResult("172.20.0.1", "2026-01-01T12:00:00Z", True, 1.0),
    ]
    import json
    with patch("geo_manager.cluster_health.set_cluster_health_state") as mock_set:
        out = update_and_get_json(["172.20.0.1"], 8080, 5.0)
    data = json.loads(out)
    assert len(data["nodes"]) == 1
    mock_set.assert_called_once()


@patch("geo_manager.cluster_health.probe_node")
def test_run_cluster_probe_probe_raises_appends_error_result(mock_probe):
    mock_probe.side_effect = RuntimeError("probe failed")
    results = run_cluster_probe(["10.0.0.1"], 8080, 2.0)
    assert len(results) == 1
    assert results[0].reachable is False
    assert results[0].error == "probe failed"


def test_cluster_health_state_to_json_dict_skips_empty_history():
    state = ClusterHealthState()
    state.results = [
        NodeProbeResult("1.2.3.4", "2026-01-01T12:00:00Z", True, 1.0),
    ]
    state.history_per_node["1.2.3.4"] = deque(maxlen=10)  # empty
    state.history_per_node["5.6.7.8"] = deque([NodeProbeResult("5.6.7.8", "2026-01-01T12:00:00Z", False, None, error="x")], maxlen=10)
    d = state.to_json_dict()
    assert "offline_summary" in d
    assert "5.6.7.8" in d["offline_summary"]
    assert d["offline_summary"]["5.6.7.8"]["failures"] == 1
    assert d["offline_summary"]["5.6.7.8"]["consecutive_failures"] == 1
