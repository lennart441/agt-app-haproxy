"""Tests for geo_manager.staging."""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from geo_manager.staging import (
    get_master_status_url,
    fetch_node_status,
    get_master_validated_at,
    should_follower_activate,
)


def test_get_master_status_url():
    assert get_master_status_url("172.20.0.1") == "http://172.20.0.1:8080/geo/status"
    assert get_master_status_url("10.0.0.1", port=9090) == "http://10.0.0.1:9090/geo/status"


@patch("urllib.request.urlopen")
def test_fetch_node_status_ok(mock_open):
    mock_open.return_value.__enter__.return_value.read.return_value = b'{"node_prio": 1, "validated_at": "2026-01-01T12:00:00Z"}'
    with patch("geo_manager.staging.json.loads") as j:
        j.return_value = {"node_prio": 1, "validated_at": "2026-01-01T12:00:00Z"}
        result = fetch_node_status("http://127.0.0.1:8080/geo/status")
    assert result is not None
    assert result.get("node_prio") == 1


def test_fetch_node_status_fail():
    with patch("urllib.request.urlopen") as m:
        m.side_effect = Exception("connection refused")
        result = fetch_node_status("http://127.0.0.1:9999/geo/status")
    assert result is None


@patch("geo_manager.staging.fetch_node_status")
def test_get_master_validated_at_finds_prio1(mock_fetch):
    mock_fetch.return_value = {"node_prio": 1, "validated_at": "2026-01-01T12:00:00+00:00"}
    result = get_master_validated_at(["172.20.0.1"], 8080)
    assert result is not None
    ip, dt = result
    assert ip == "172.20.0.1"
    assert dt is not None


@patch("geo_manager.staging.fetch_node_status")
def test_get_master_validated_at_invalid_date_returns_none_dt(mock_fetch):
    mock_fetch.return_value = {"node_prio": 1, "validated_at": "not-a-date"}
    result = get_master_validated_at(["172.20.0.1"], 8080)
    assert result is not None
    _, dt = result
    assert dt is None


@patch("geo_manager.staging.fetch_node_status")
def test_get_master_validated_at_date_without_tz_gets_utc(mock_fetch):
    mock_fetch.return_value = {"node_prio": 1, "validated_at": "2026-01-01T12:00:00"}
    result = get_master_validated_at(["172.20.0.1"], 8080)
    assert result is not None
    _, dt = result
    assert dt is not None
    assert dt.tzinfo is not None


@patch("geo_manager.staging.fetch_node_status")
def test_get_master_validated_at_no_validated_at(mock_fetch):
    mock_fetch.return_value = {"node_prio": 1}
    result = get_master_validated_at(["172.20.0.1"], 8080)
    assert result is not None
    ip, dt = result
    assert ip == "172.20.0.1"
    assert dt is None


@patch("geo_manager.staging.fetch_node_status")
def test_get_master_validated_at_skips_non_prio1(mock_fetch):
    mock_fetch.return_value = {"node_prio": 2}
    result = get_master_validated_at(["172.20.0.1"], 8080)
    assert result is None


@patch("geo_manager.staging.fetch_node_status")
def test_get_master_validated_at_all_unreachable(mock_fetch):
    mock_fetch.return_value = None
    result = get_master_validated_at(["172.20.0.1", "172.20.0.2"], 8080)
    assert result is None


def test_should_follower_activate_prio1():
    assert should_follower_activate(1, datetime.now(timezone.utc), 48) is True


def test_should_follower_activate_no_master_validated_at():
    assert should_follower_activate(2, None, 48) is False


def test_should_follower_activate_delay_zero():
    """Delay 0 → activate; with local_validated_at set so the delay branch is hit."""
    now = datetime.now(timezone.utc)
    assert should_follower_activate(2, now, 0) is True
    # Update mode with delay 0: still True (covers stage_delay_hours <= 0 return)
    assert should_follower_activate(2, now, 0, local_validated_at=now) is True


def test_should_follower_activate_elapsed():
    """Update mode: master validated 50h ago, delay 48h → activate."""
    from datetime import timedelta
    old = datetime.now(timezone.utc) - timedelta(hours=50)
    local = datetime.now(timezone.utc) - timedelta(hours=24)
    assert should_follower_activate(2, old, 48, local_validated_at=local) is True


def test_should_follower_activate_not_elapsed():
    """With local_validated_at set (update mode), delay applies; 1h ago < 48h → False."""
    from datetime import timedelta
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    local = datetime.now(timezone.utc) - timedelta(hours=24)
    assert should_follower_activate(2, recent, 48, local_validated_at=local) is False


def test_should_follower_activate_bootstrap():
    """Bootstrap: no local map yet (local_validated_at=None) → activate immediately when master has data."""
    from datetime import timedelta
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    assert should_follower_activate(2, recent, 48, local_validated_at=None) is True
    # No master data → still False
    assert should_follower_activate(2, None, 48, local_validated_at=None) is False


def test_should_follower_activate_update_mode_elapsed_hours():
    """Update mode: local_validated_at set; elapsed hours >= delay → True (covers delay calculation)."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    master_old = now - timedelta(hours=100)
    local_any = now - timedelta(hours=1)
    assert should_follower_activate(2, master_old, 48, local_validated_at=local_any) is True
    master_recent = now - timedelta(hours=1)
    assert should_follower_activate(2, master_recent, 48, local_validated_at=local_any) is False
