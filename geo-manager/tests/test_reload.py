"""Tests for geo_manager.reload."""
import os
from unittest.mock import patch

import pytest

from geo_manager.reload import trigger_reload


def test_trigger_reload_socket_missing():
    """Ohne Wartezeit sofort False, wenn Socket fehlt."""
    assert trigger_reload("/nonexistent/socket", wait_for_socket_sec=0) is False


@patch("os.path.exists")
@patch("geo_manager.reload.subprocess.run")
def test_trigger_reload_success(mock_run, mock_exists):
    mock_exists.return_value = True
    mock_run.return_value = type("R", (), {"returncode": 0, "stderr": ""})()
    assert trigger_reload("/var/run/haproxy.sock") is True


@patch("os.path.exists")
@patch("geo_manager.reload.subprocess.run")
def test_trigger_reload_failure(mock_run, mock_exists):
    mock_exists.return_value = True
    mock_run.return_value = type("R", (), {"returncode": 1, "stderr": "error"})()
    assert trigger_reload("/var/run/haproxy.sock") is False


@patch("os.path.exists")
@patch("geo_manager.reload.subprocess.run")
def test_trigger_reload_socat_not_found(mock_run, mock_exists):
    mock_exists.return_value = True
    mock_run.side_effect = FileNotFoundError
    assert trigger_reload("/var/run/haproxy.sock") is False


@patch("os.path.exists")
@patch("geo_manager.reload.subprocess.run")
def test_trigger_reload_timeout(mock_run, mock_exists):
    import subprocess
    mock_exists.return_value = True
    mock_run.side_effect = subprocess.TimeoutExpired("socat", 10)
    assert trigger_reload("/var/run/haproxy.sock") is False


@patch("geo_manager.reload.time.sleep")
@patch("os.path.exists")
@patch("geo_manager.reload.subprocess.run")
def test_trigger_reload_waits_for_socket_then_succeeds(mock_run, mock_exists, mock_sleep):
    """Socket erscheint nach kurzer Wartezeit → Reload klappt."""
    mock_exists.side_effect = [False, False, True]
    mock_run.return_value = type("R", (), {"returncode": 0, "stderr": ""})()
    assert trigger_reload("/var/run/sock", wait_for_socket_sec=10) is True
    assert mock_sleep.call_count == 2
