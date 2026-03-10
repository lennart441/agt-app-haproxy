"""Tests for main.py HTTP handler and helpers."""
import json
import runpy
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from geo_manager.main import (
    GeoStatusHandler,
    get_validated_at,
    set_validated_at,
    run_master_loop,
    run_follower_loop,
    _master_fetch_validate_activate,
)
from geo_manager.config import Config


def test_set_get_validated_at():
    set_validated_at(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    assert get_validated_at() is not None
    assert get_validated_at().year == 2026


def test_geo_status_handler_404():
    """Handler returns 404 for non-/geo/status path."""
    handler = MagicMock()
    handler.path = "/other"
    handler.server = MagicMock()
    handler.server.config = MagicMock()
    GeoStatusHandler.do_GET(handler)
    handler.send_error.assert_called_once_with(404)


def test_geo_deploy_now_post_404():
    """POST to path other than /geo/deploy-now returns 404."""
    handler = MagicMock()
    handler.path = "/other"
    handler.server = MagicMock()
    handler.server.config = MagicMock()
    GeoStatusHandler.do_POST(handler)
    handler.send_error.assert_called_once_with(404)


def test_geo_deploy_now_forbidden_for_follower():
    """POST /geo/deploy-now returns 403 on non-master nodes."""
    handler = MagicMock()
    handler.path = "/geo/deploy-now"
    handler.server = MagicMock()
    config = MagicMock()
    config.am_i_master.return_value = False
    handler.server.config = config
    # Bind real method so do_POST's self._handle_geo_deploy_now runs the implementation
    handler._handle_geo_deploy_now = GeoStatusHandler._handle_geo_deploy_now.__get__(handler, GeoStatusHandler)
    GeoStatusHandler.do_POST(handler)
    handler.send_error.assert_called_once()
    code, _msg = handler.send_error.call_args[0]
    assert code == 403


def test_geo_deploy_now_master_success(monkeypatch):
    """POST /geo/deploy-now on master triggers _master_fetch_validate_activate."""
    handler = MagicMock()
    handler.path = "/geo/deploy-now"
    handler.server = MagicMock()
    config = Config.from_env()
    config.node_prio = 1
    handler.server.config = config
    # Bind real method so do_POST's self._handle_geo_deploy_now runs the implementation
    handler._handle_geo_deploy_now = GeoStatusHandler._handle_geo_deploy_now.__get__(handler, GeoStatusHandler)

    from geo_manager import main as main_mod

    called = {}

    def fake_master_activate(cfg):
        called["run"] = True

    monkeypatch.setattr(main_mod, "_master_fetch_validate_activate", fake_master_activate)
    GeoStatusHandler.do_POST(handler)
    assert called.get("run") is True


def test_geo_deploy_now_master_exception_returns_500(monkeypatch):
    """POST /geo/deploy-now on master when _master_fetch_validate_activate raises returns 500."""
    handler = MagicMock()
    handler.path = "/geo/deploy-now"
    handler.server = MagicMock()
    config = Config.from_env()
    config.node_prio = 1
    handler.server.config = config
    handler._handle_geo_deploy_now = GeoStatusHandler._handle_geo_deploy_now.__get__(handler, GeoStatusHandler)

    from geo_manager import main as main_mod

    def fake_master_activate_raise(cfg):
        raise RuntimeError("fetch failed")

    monkeypatch.setattr(main_mod, "_master_fetch_validate_activate", fake_master_activate_raise)
    GeoStatusHandler.do_POST(handler)
    handler.send_response.assert_called_once_with(500)
    handler.wfile.write.assert_called_once()
    written = handler.wfile.write.call_args[0][0]
    assert b"Geo deploy failed" in written
    assert b"fetch failed" in written


def test_health_handler_200():
    """GET /health returns 200 OK."""
    handler = MagicMock()
    handler.path = "/health"
    handler.server = MagicMock()
    handler._send_health = lambda: GeoStatusHandler._send_health(handler)
    handler.wfile = MagicMock()
    GeoStatusHandler.do_GET(handler)
    handler.send_response.assert_called_once_with(200)
    handler.wfile.write.assert_called_once_with(b"OK")


def test_metrics_handler_200():
    """GET /metrics returns Prometheus text."""
    handler = MagicMock()
    handler.path = "/metrics"
    handler.server = MagicMock()
    handler._send_metrics = lambda: GeoStatusHandler._send_metrics(handler)
    handler.wfile = MagicMock()
    with patch("geo_manager.main.get_cluster_health_state") as mock_get:
        mock_get.return_value = None
        GeoStatusHandler.do_GET(handler)
    handler.send_response.assert_called_once_with(200)
    handler.wfile.write.assert_called_once_with(b"")


def test_metrics_handler_200_with_state():
    """GET /metrics with cluster state returns Prometheus body."""
    from geo_manager.cluster_health import ClusterHealthState, NodeProbeResult
    state = ClusterHealthState()
    state.update([NodeProbeResult("1.2.3.4", "2026-01-01T12:00:00Z", True, 2.0)])
    handler = MagicMock()
    handler.path = "/metrics"
    handler.server = MagicMock()
    handler._send_metrics = lambda: GeoStatusHandler._send_metrics(handler)
    handler.wfile = MagicMock()
    with patch("geo_manager.main.get_cluster_health_state") as mock_get:
        mock_get.return_value = state
        GeoStatusHandler.do_GET(handler)
    handler.send_response.assert_called_once_with(200)
    body = handler.wfile.write.call_args[0][0]
    assert b"geo_cluster_node_reachable" in body


def test_cluster_handler_200():
    """GET /cluster returns JSON cluster health."""
    handler = MagicMock()
    handler.path = "/cluster"
    handler.server = MagicMock()
    config = Config.from_env()
    handler.server.config = config
    handler._send_cluster = lambda c: GeoStatusHandler._send_cluster(handler, c)
    handler.wfile = MagicMock()
    with patch("geo_manager.main.get_cluster_health_state") as mock_get:
        mock_get.return_value = None
        GeoStatusHandler.do_GET(handler)
    handler.send_response.assert_called_once_with(200)
    body = handler.wfile.write.call_args[0][0]
    data = json.loads(body.decode("utf-8"))
    assert "nodes" in data
    assert "last_probe_at" in data


def test_cluster_handler_200_with_state():
    """GET /cluster with state returns full JSON."""
    from geo_manager.cluster_health import ClusterHealthState, NodeProbeResult
    state = ClusterHealthState()
    state.update([NodeProbeResult("172.20.0.1", "2026-01-01T12:00:00Z", True, 1.5)])
    handler = MagicMock()
    handler.path = "/cluster"
    handler.server = MagicMock()
    config = Config.from_env()
    handler.server.config = config
    handler._send_cluster = lambda c: GeoStatusHandler._send_cluster(handler, c)
    handler.wfile = MagicMock()
    with patch("geo_manager.main.get_cluster_health_state") as mock_get:
        mock_get.return_value = state
        GeoStatusHandler.do_GET(handler)
    body = handler.wfile.write.call_args[0][0]
    data = json.loads(body.decode("utf-8"))
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["latency_ms"] == 1.5


def test_geo_status_handler_200():
    """Handler returns JSON for GET /geo/status."""
    handler = MagicMock()
    handler.path = "/geo/status"
    handler.server = MagicMock()
    handler.server.config = MagicMock()
    handler.server.config.node_prio = 1
    handler.server.config.node_name = "agt-1"
    handler._send_geo_status = lambda c: GeoStatusHandler._send_geo_status(handler, c)
    handler.wfile = MagicMock()
    set_validated_at(None)
    GeoStatusHandler.do_GET(handler)
    handler.send_response.assert_called_once_with(200)
    handler.send_header.assert_any_call("Content-Type", "application/json")
    write_calls = handler.wfile.write.call_args_list
    assert len(write_calls) == 1
    body = write_calls[0][0][0]
    data = json.loads(body.decode("utf-8"))
    assert data["node_prio"] == 1
    assert data["node_name"] == "agt-1"
    assert "validated_at" in data


def test_geo_status_handler_200_with_validated_at():
    handler = MagicMock()
    handler.path = "/geo/status"
    handler.server = MagicMock()
    handler.server.config = MagicMock()
    handler.server.config.node_prio = 1
    handler.server.config.node_name = "agt-1"
    handler._send_geo_status = lambda c: GeoStatusHandler._send_geo_status(handler, c)
    handler.wfile = MagicMock()
    set_validated_at(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    GeoStatusHandler.do_GET(handler)
    write_calls = handler.wfile.write.call_args_list
    data = json.loads(write_calls[0][0][0].decode("utf-8"))
    assert data["validated_at"] is not None
    assert "2026" in data["validated_at"]


def test_log_message():
    handler = MagicMock()
    handler.address_string.return_value = "127.0.0.1"
    GeoStatusHandler.log_message(handler, "GET %s", "/geo/status")
    # Just ensure it doesn't raise; log_message uses logger.debug


def test_run_master_loop_no_url_exits_early(monkeypatch):
    monkeypatch.setenv("GEO_SOURCE_URL", "")
    config = Config.from_env()
    run_master_loop(config)


def test_run_master_loop_os_nice_fails_continues(monkeypatch):
    """When os.nice raises (e.g. Windows), loop still runs."""
    monkeypatch.setenv("GEO_SOURCE_URL", "")
    config = Config.from_env()
    with patch("geo_manager.main.os.nice", side_effect=AttributeError("nice not available")):
        run_master_loop(config)


@patch("geo_manager.main._master_fetch_validate_activate")
def test_run_master_loop_with_url_calls_fetch_then_sleep(mock_activate, monkeypatch):
    monkeypatch.setenv("GEO_SOURCE_URL", "http://example.com/geo.csv")
    config = Config.from_env()
    with patch("geo_manager.main.time.sleep", side_effect=[None, StopIteration]):
        with pytest.raises(StopIteration):
            run_master_loop(config)
    assert mock_activate.call_count == 2


@patch("geo_manager.main._master_fetch_validate_activate")
def test_run_master_loop_exception_logs_and_continues(mock_activate, monkeypatch):
    monkeypatch.setenv("GEO_SOURCE_URL", "http://example.com/geo.csv")
    config = Config.from_env()
    mock_activate.side_effect = [Exception("fail"), Exception("stop")]
    with patch("geo_manager.main.time.sleep", side_effect=[None, StopIteration]):
        with pytest.raises(StopIteration):
            run_master_loop(config)
    assert mock_activate.call_count == 2


def test_main_starts_server_and_serve_forever():
    with patch("geo_manager.main.HTTPServer") as mock_http:
        with patch("geo_manager.main.threading.Thread"):
            mock_server = MagicMock()
            mock_http.return_value = mock_server
            mock_server.serve_forever.side_effect = StopIteration("stop")
            with pytest.raises(StopIteration):
                from geo_manager.main import main
                main()
    mock_server.serve_forever.assert_called_once()


def test_main_signal_handler_calls_server_shutdown():
    """Graceful shutdown: SIGTERM/SIGINT handler calls server.shutdown()."""
    with patch("geo_manager.main.HTTPServer") as mock_http:
        with patch("geo_manager.main.threading.Thread"):
            with patch("geo_manager.main.signal.signal") as mock_signal:
                mock_server = MagicMock()
                mock_http.return_value = mock_server
                mock_server.serve_forever.side_effect = StopIteration("stop")
                with pytest.raises(StopIteration):
                    from geo_manager.main import main
                    main()
                assert mock_signal.call_count >= 2
                handler = mock_signal.call_args_list[0][0][1]
                handler()
                mock_server.shutdown.assert_called_once()


def test_main_starts_follower_thread_when_not_master():
    with patch("geo_manager.main.HTTPServer") as mock_http:
        with patch("geo_manager.main.threading.Thread") as mock_thread:
            mock_server = MagicMock()
            mock_http.return_value = mock_server
            mock_server.serve_forever.side_effect = StopIteration("stop")
            config = Config.from_env()
            config.node_prio = 2
            config.am_i_master = lambda: False
            with patch("geo_manager.main.Config.from_env", return_value=config):
                with pytest.raises(StopIteration):
                    from geo_manager.main import main
                    main()
    mock_thread.assert_called_once()
    assert mock_thread.call_args[1]["target"].__name__ == "run_follower_loop"


def test_main_starts_cluster_health_thread_when_mesh_nodes_set():
    with patch("geo_manager.main.HTTPServer") as mock_http:
        with patch("geo_manager.main.threading.Thread") as mock_thread:
            mock_server = MagicMock()
            mock_http.return_value = mock_server
            mock_server.serve_forever.side_effect = StopIteration("stop")
            config = Config.from_env()
            config.mesh_nodes = ["172.20.0.1"]
            with patch("geo_manager.main.Config.from_env", return_value=config):
                with pytest.raises(StopIteration):
                    from geo_manager.main import main
                    main()
    assert mock_thread.call_count == 2
    assert mock_thread.call_args_list[1][1]["target"].__name__ == "run_cluster_health_loop"


def test_main_module_main_block():
    """Cover the if __name__ == '__main__': main() block in main.py."""
    import geo_manager.main as main_mod
    with patch.object(main_mod, "main", side_effect=SystemExit(0)):
        with pytest.raises(SystemExit):
            exec("if __name__ == '__main__': main()", {**vars(main_mod), "__name__": "__main__"})


@patch("geo_manager.main.get_master_validated_at")
@patch("geo_manager.main._master_fetch_validate_activate")
def test_run_follower_loop_activates_when_ready(mock_activate, mock_get_master):
    from datetime import timedelta
    config = Config.from_env()
    config.node_prio = 2
    config.mesh_nodes = ["172.20.0.1"]
    old = datetime.now(timezone.utc) - timedelta(hours=50)
    mock_get_master.return_value = ("172.20.0.1", old)
    # Run one iteration by patching time.sleep to raise after first iteration
    with patch("geo_manager.main.time.sleep", side_effect=[None, StopIteration]):
        with pytest.raises(StopIteration):
            run_follower_loop(config)
    mock_activate.assert_called()


@patch("geo_manager.main.trigger_reload")
@patch("geo_manager.main.validate_syntax_with_config")
@patch("geo_manager.main.validate_anchors")
@patch("geo_manager.main.validate_size")
@patch("geo_manager.main.write_maps")
@patch("geo_manager.main.build_whitelist_map")
@patch("geo_manager.main.fetch_geo_csv_to_map")
def test_master_fetch_validate_activate_uses_blocks_and_locations(
    mock_csv, mock_whitelist, mock_write, mock_size, mock_anchors, mock_syntax_with_config, mock_reload, tmp_path, monkeypatch
):
    monkeypatch.setenv("GEO_BLOCKS_URL", "http://a/blocks.csv")
    monkeypatch.setenv("GEO_LOCATIONS_URL", "http://a/loc.csv")
    mock_csv.return_value = "\n".join([f"{i}.0.0.0/24\tDE" for i in range(50)])
    mock_whitelist.return_value = ""
    mock_size.return_value = True
    mock_anchors.return_value = True
    mock_syntax_with_config.return_value = True
    mock_reload.return_value = True
    config = Config.from_env()
    config.map_dir = str(tmp_path)
    config.haproxy_cfg_path = str(tmp_path / "x.cfg")
    config.anchor_ips = []
    _master_fetch_validate_activate(config)
    mock_csv.assert_called_once()


@patch("geo_manager.main.trigger_reload")
@patch("geo_manager.main.validate_syntax_with_config")
@patch("geo_manager.main.validate_anchors")
@patch("geo_manager.main.validate_size")
@patch("geo_manager.main.write_maps")
@patch("geo_manager.main.build_whitelist_map")
@patch("geo_manager.main.fetch_geo_from_single_url")
@patch("geo_manager.main.fetch_geo_csv_to_map")
def test_master_fetch_validate_activate_success(
    mock_csv, mock_single, mock_whitelist, mock_write, mock_size, mock_anchors, mock_syntax_with_config, mock_reload,
    tmp_path,
):
    mock_whitelist.return_value = "8.8.8.8\t1\n"
    # Mind. 50 Zeilen, damit kein Fail-Open (Normalpfad)
    geo_50 = "\n".join([f"{i}.0.0.0/24\tDE" for i in range(50)])
    mock_single.return_value = geo_50 + "\n8.8.8.8/32\tDE\n"
    mock_csv.side_effect = Exception("not used")
    mock_size.return_value = True
    mock_anchors.return_value = True
    mock_syntax_with_config.return_value = True
    mock_reload.return_value = True
    config = Config.from_env()
    config.geo_source_url = "http://example.com/geo.csv"
    config.map_dir = str(tmp_path)
    config.haproxy_cfg_path = str(tmp_path / "haproxy.cfg")
    config.anchor_ips = ["8.8.8.8"]
    (tmp_path / "geo.map").write_text("old")
    _master_fetch_validate_activate(config)
    mock_write.assert_called()
    mock_reload.assert_called_once()
    assert not (tmp_path / "geo.map.bak").exists()


@patch("geo_manager.main.trigger_reload")
@patch("geo_manager.main.validate_syntax_with_config")
@patch("geo_manager.main.validate_anchors")
@patch("geo_manager.main.validate_size")
@patch("geo_manager.main.write_maps")
@patch("geo_manager.main.build_whitelist_map")
@patch("geo_manager.main.merge_geo_map_contents")
@patch("geo_manager.main.fetch_geo_from_single_url")
def test_master_fetch_validate_activate_single_url_with_ipv6_merge(
    mock_single, mock_merge, mock_whitelist, mock_write, mock_size, mock_anchors, mock_syntax_with_config, mock_reload, tmp_path
):
    """When GEO_SOURCE_IPV6_URL is set, both URLs are fetched and merged."""
    geo_50 = "\n".join([f"{i}.0.0.0/24\tDE" for i in range(50)])
    mock_single.side_effect = [geo_50 + "\n8.8.8.8/32\tDE\n", "2001:db8::/32\tDE\n"]
    mock_merge.return_value = geo_50 + "\n8.8.8.8/32\tDE\n2001:db8::/32\tDE\n"
    mock_whitelist.return_value = "8.8.8.8\t1\n"
    mock_size.return_value = True
    mock_anchors.return_value = True
    mock_syntax_with_config.return_value = True
    mock_reload.return_value = True
    config = Config.from_env()
    config.geo_source_url = "http://example.com/geo.csv"
    config.geo_source_ipv6_url = "http://example.com/geo-ipv6.csv"
    config.map_dir = str(tmp_path)
    config.haproxy_cfg_path = str(tmp_path / "haproxy.cfg")
    config.anchor_ips = ["8.8.8.8"]
    (tmp_path / "geo.map").write_text("old")
    _master_fetch_validate_activate(config)
    assert mock_single.call_count == 2
    mock_merge.assert_called_once()
    call_args = mock_merge.call_args[0]
    assert "8.8.8.8/32" in call_args[0]
    assert "2001:db8::/32" in call_args[1]
    mock_reload.assert_called_once()


@patch("geo_manager.main.validate_size")
@patch("geo_manager.main.fetch_geo_from_single_url")
def test_master_fetch_validate_activate_size_fail(mock_single, mock_size):
    # Mind. 50 Zeilen, damit kein Fail-Open und validate_size aufgerufen wird
    mock_single.return_value = "\n".join([f"{i}.0.0.0/24\tDE" for i in range(50)])
    mock_size.return_value = False
    config = Config.from_env()
    config.geo_source_url = "http://example.com/geo.csv"
    with pytest.raises(RuntimeError, match="Size check"):
        _master_fetch_validate_activate(config)


@patch("geo_manager.main.trigger_reload")
@patch("geo_manager.main.validate_syntax_with_config")
@patch("geo_manager.main.validate_anchors")
@patch("geo_manager.main.validate_size")
@patch("geo_manager.main.write_maps")
@patch("geo_manager.main.build_whitelist_map")
@patch("geo_manager.main.fetch_geo_from_single_url")
def test_master_fetch_validate_activate_anchor_ips_empty_skips_check(
    mock_single, mock_whitelist, mock_write, mock_size, mock_anchors, mock_syntax_with_config, mock_reload,
    tmp_path,
):
    """When anchor_ips is empty, validate_anchors is not called (check skipped)."""
    mock_single.return_value = "1.0.0.0/24\tDE\n"
    mock_whitelist.return_value = ""
    mock_size.return_value = True
    mock_syntax_with_config.return_value = True
    mock_reload.return_value = True
    config = Config.from_env()
    config.geo_source_url = "http://example.com/geo.csv"
    config.map_dir = str(tmp_path)
    config.haproxy_cfg_path = str(tmp_path / "x.cfg")
    config.anchor_ips = []
    (tmp_path / "geo.map").write_text("old")
    _master_fetch_validate_activate(config)
    mock_anchors.assert_not_called()
    mock_reload.assert_called_once()


@patch("geo_manager.main.validate_anchors")
@patch("geo_manager.main.validate_size")
@patch("geo_manager.main.fetch_geo_from_single_url")
def test_master_fetch_validate_activate_anchor_fail(
    mock_single, mock_size, mock_anchors, tmp_path
):
    mock_single.return_value = "1.0.0.0/24\tDE\n"
    mock_size.return_value = True
    mock_anchors.return_value = False
    config = Config.from_env()
    config.geo_source_url = "http://example.com/geo.csv"
    config.map_dir = str(tmp_path)
    config.haproxy_cfg_path = str(tmp_path / "x.cfg")
    config.anchor_ips = ["8.8.8.8"]
    with pytest.raises(RuntimeError, match="Anchor check"):
        _master_fetch_validate_activate(config)


@patch("geo_manager.main.trigger_reload")
@patch("geo_manager.main.validate_syntax_with_config")
@patch("geo_manager.main.validate_anchors")
@patch("geo_manager.main.write_maps")
@patch("geo_manager.main.build_whitelist_map")
@patch("geo_manager.main.fetch_geo_from_single_url")
def test_master_fetch_validate_activate_fail_open_empty_content(
    mock_single, mock_whitelist, mock_write, mock_anchors, mock_syntax_with_config, mock_reload, tmp_path
):
    """Bei leerer Geo-Liste: Fail-open, permissive Map schreiben, kein Abbruch."""
    mock_single.return_value = ""
    mock_whitelist.return_value = ""
    mock_anchors.return_value = True
    mock_syntax_with_config.return_value = True
    mock_reload.return_value = True
    config = Config.from_env()
    config.geo_source_url = "http://example.com/geo.csv"
    config.map_dir = str(tmp_path)
    config.haproxy_cfg_path = str(tmp_path / "x.cfg")
    config.anchor_ips = []
    _master_fetch_validate_activate(config)
    # write_maps(map_dir, geo_content, whitelist_content, ...) → Index 1 = geo_content
    call_args = mock_write.call_args
    geo_content = call_args[0][1]
    assert "0.0.0.0/0\t" in geo_content
    assert "::/0\t" in geo_content
    mock_reload.assert_called_once()


@patch("geo_manager.main.trigger_reload")
@patch("geo_manager.main.validate_syntax_with_config")
@patch("geo_manager.main.validate_anchors")
@patch("geo_manager.main.write_maps")
@patch("geo_manager.main.build_whitelist_map")
@patch("geo_manager.main.fetch_geo_from_single_url")
def test_master_fetch_validate_activate_fail_open_few_entries(
    mock_single, mock_whitelist, mock_write, mock_anchors, mock_syntax_with_config, mock_reload, tmp_path
):
    """Bei weniger als 50 Einträgen: Fail-open, permissive Map."""
    mock_single.return_value = "1.0.0.0/24\tDE\n2.0.0.0/24\tAT\n"
    mock_whitelist.return_value = ""
    mock_anchors.return_value = True
    mock_syntax_with_config.return_value = True
    mock_reload.return_value = True
    config = Config.from_env()
    config.geo_source_url = "http://example.com/geo.csv"
    config.map_dir = str(tmp_path)
    config.haproxy_cfg_path = str(tmp_path / "x.cfg")
    config.anchor_ips = []
    _master_fetch_validate_activate(config)
    call_args = mock_write.call_args
    geo_content = call_args[0][1]
    assert "0.0.0.0/0\t" in geo_content
    assert "::/0\t" in geo_content
    mock_reload.assert_called_once()


@patch("geo_manager.main.trigger_reload")
@patch("geo_manager.main.validate_syntax_with_config")
@patch("geo_manager.main.write_maps")
@patch("geo_manager.main.build_whitelist_map")
@patch("geo_manager.main.fetch_geo_from_single_url")
def test_master_fetch_validate_activate_syntax_fail_restores_backup(
    mock_single, mock_whitelist, mock_write, mock_syntax_with_config, mock_reload, tmp_path
):
    mock_single.return_value = "1.0.0.0/24\tDE\n"
    mock_whitelist.return_value = ""
    mock_syntax_with_config.return_value = False
    (tmp_path / "geo.map").write_text("old")
    config = Config.from_env()
    config.geo_source_url = "http://example.com/geo.csv"
    config.map_dir = str(tmp_path)
    config.haproxy_cfg_path = str(tmp_path / "x.cfg")
    config.anchor_ips = []
    with pytest.raises(RuntimeError, match="Syntax check"):
        _master_fetch_validate_activate(config)
    assert (tmp_path / "geo.map").read_text() == "old"


@patch("geo_manager.main.trigger_reload")
@patch("geo_manager.main.validate_syntax_with_config")
@patch("geo_manager.main.validate_anchors")
@patch("geo_manager.main.validate_size")
@patch("geo_manager.main.write_maps")
@patch("geo_manager.main.build_whitelist_map")
@patch("geo_manager.main.fetch_geo_from_single_url")
def test_master_fetch_validate_activate_reload_fails(
    mock_single, mock_whitelist, mock_write, mock_size, mock_anchors, mock_syntax_with_config, mock_reload, tmp_path
):
    mock_single.return_value = "1.0.0.0/24\tDE\n"
    mock_whitelist.return_value = ""
    mock_size.return_value = True
    mock_anchors.return_value = True
    mock_syntax_with_config.return_value = True
    mock_reload.return_value = False
    config = Config.from_env()
    config.geo_source_url = "http://example.com/geo.csv"
    config.map_dir = str(tmp_path)
    config.haproxy_cfg_path = str(tmp_path / "x.cfg")
    config.anchor_ips = []
    with pytest.raises(RuntimeError, match="reload"):
        _master_fetch_validate_activate(config)
    mock_reload.assert_called_once()


def test_run_follower_loop_prio1_returns_immediately():
    config = Config.from_env()
    config.node_prio = 1
    run_follower_loop(config)


def test_run_follower_loop_os_nice_fails_continues():
    """When os.nice raises (e.g. Windows), follower loop still runs."""
    config = Config.from_env()
    config.node_prio = 1
    with patch("geo_manager.main.os.nice", side_effect=OSError(1, "Permission denied")):
        run_follower_loop(config)


@patch("geo_manager.main._master_fetch_validate_activate")
@patch("geo_manager.main.should_follower_activate")
@patch("geo_manager.main.get_master_validated_at")
def test_run_follower_loop_result_none_continues(mock_get, mock_should, mock_activate):
    config = Config.from_env()
    config.node_prio = 2
    config.mesh_nodes = ["172.20.0.1"]
    mock_get.return_value = None
    with patch("geo_manager.main.time.sleep", side_effect=[None, StopIteration]):
        with pytest.raises(StopIteration):
            run_follower_loop(config)
    mock_activate.assert_not_called()


@patch("geo_manager.main._master_fetch_validate_activate")
@patch("geo_manager.main.should_follower_activate")
@patch("geo_manager.main._master_fetch_validate_activate")
@patch("geo_manager.main.should_follower_activate")
@patch("geo_manager.main.get_master_validated_at")
def test_run_follower_loop_exception_logged_and_continues(mock_get, mock_should, mock_activate, *_):
    config = Config.from_env()
    config.node_prio = 2
    config.mesh_nodes = ["172.20.0.1"]
    mock_get.side_effect = Exception("err")
    with patch("geo_manager.main.time.sleep", side_effect=[None, StopIteration]):
        with pytest.raises(StopIteration):
            run_follower_loop(config)
    mock_activate.assert_not_called()


@patch("geo_manager.main.send_failure_mail")
@patch("geo_manager.main._master_fetch_validate_activate")
@patch("geo_manager.main.time.sleep")
def test_run_master_loop_failure_after_retries_sends_mail(mock_sleep, mock_activate, mock_mail, monkeypatch):
    monkeypatch.setenv("GEO_SOURCE_URL", "http://example.com/geo.csv")
    monkeypatch.setenv("FETCH_RETRIES", "2")
    monkeypatch.setenv("FETCH_RETRY_DELAY_SEC", "0.1")
    config = Config.from_env()
    mock_activate.side_effect = RuntimeError("fetch failed")
    mock_sleep.side_effect = [None, StopIteration]  # retry_delay, then interval_sec
    with pytest.raises(StopIteration):
        run_master_loop(config)
    assert mock_activate.call_count == 2
    mock_mail.assert_called_once()


def test_run_cluster_health_loop_empty_mesh_returns_immediately():
    from geo_manager.main import run_cluster_health_loop
    config = Config.from_env()
    config.mesh_nodes = []
    run_cluster_health_loop(config)


@patch("geo_manager.main.send_failure_mail")
def test_notify_fetch_failure_handles_mail_exception(mock_mail):
    from geo_manager.main import _notify_fetch_failure
    mock_mail.side_effect = RuntimeError("smtp down")
    config = Config.from_env()
    config.mail_enabled = True
    config.mail_host = "x"
    config.mail_to = ["a@b.com"]
    _notify_fetch_failure(config, "error detail")
    mock_mail.assert_called_once()


@patch("geo_manager.main.run_cluster_probe")
@patch("geo_manager.main.time.sleep")
def test_run_cluster_health_loop_updates_state(mock_sleep, mock_probe):
    from geo_manager.cluster_health import NodeProbeResult, get_cluster_health_state
    mock_probe.return_value = [
        NodeProbeResult("172.20.0.1", "2026-01-01T12:00:00Z", True, 1.0),
    ]
    mock_sleep.side_effect = [None, StopIteration]
    config = Config.from_env()
    config.mesh_nodes = ["172.20.0.1"]
    config.cluster_health_interval_hours = 0.001
    with pytest.raises(StopIteration):
        from geo_manager.main import run_cluster_health_loop
        run_cluster_health_loop(config)
    mock_probe.assert_called()
    assert get_cluster_health_state() is not None


@patch("geo_manager.main.run_cluster_probe")
@patch("geo_manager.main.time.sleep")
def test_run_cluster_health_loop_exception_logs_and_continues(mock_sleep, mock_probe):
    from geo_manager.main import run_cluster_health_loop
    mock_probe.side_effect = [RuntimeError("probe error"), StopIteration]
    mock_sleep.side_effect = [None, StopIteration]
    config = Config.from_env()
    config.mesh_nodes = ["172.20.0.1"]
    config.cluster_health_interval_hours = 0.001
    with pytest.raises(StopIteration):
        run_cluster_health_loop(config)
    assert mock_probe.call_count == 2


@patch("geo_manager.main.send_failure_mail")
@patch("geo_manager.main._master_fetch_validate_activate")
@patch("geo_manager.main.should_follower_activate")
@patch("geo_manager.main.get_master_validated_at")
@patch("geo_manager.main.time.sleep")
def test_run_follower_loop_failure_after_retries_sends_mail(mock_sleep, mock_get, mock_should, mock_activate, mock_mail):
    from datetime import timedelta
    config = Config.from_env()
    config.node_prio = 2
    config.mesh_nodes = ["172.20.0.1"]
    config.fetch_retries = 2
    config.fetch_retry_delay_sec = 0.01
    mock_get.return_value = ("172.20.0.1", datetime.now(timezone.utc) - timedelta(hours=50))
    mock_should.return_value = True
    mock_activate.side_effect = RuntimeError("fetch failed")
    mock_sleep.side_effect = [None, StopIteration]  # retry_delay in first iteration, then poll_interval
    with pytest.raises(StopIteration):
        run_follower_loop(config)
    # Initial iteration runs immediately; 2 retries = 2 activate attempts (then sleep raises)
    assert mock_activate.call_count == 2
    mock_mail.assert_called_once()


@patch("geo_manager.main._master_fetch_validate_activate")
@patch("geo_manager.main.should_follower_activate")
@patch("geo_manager.main.get_master_validated_at")
def test_run_follower_loop_should_not_activate_continues(mock_get, mock_should, mock_activate):
    config = Config.from_env()
    config.node_prio = 2
    config.mesh_nodes = ["172.20.0.1"]
    mock_get.return_value = ("172.20.0.1", None)
    mock_should.return_value = False
    with patch("geo_manager.main.time.sleep", side_effect=[None, StopIteration]):
        with pytest.raises(StopIteration):
            run_follower_loop(config)
    mock_activate.assert_not_called()
