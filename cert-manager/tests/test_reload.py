"""Tests for cert_manager.reload."""

import subprocess
from unittest.mock import MagicMock, patch

from cert_manager.reload import (
    CERT_SOCKET_TIMEOUT_SEC,
    _wait_for_socket,
    apply_ssl_cert,
)


def test_apply_ssl_cert_skips_when_socket_path_empty():
    assert apply_ssl_cert("", "/etc/ssl/certs/haproxy.pem", b"pem") is True


@patch("cert_manager.reload.os.path.exists", return_value=False)
@patch("cert_manager.reload.time.sleep")
def test_apply_ssl_cert_socket_missing_returns_true(mock_sleep, mock_exists):
    assert (
        apply_ssl_cert("/var/run/haproxy-stat/socket", "/crt.pem", b"pem", 0)
        is True
    )
    mock_sleep.assert_not_called()


@patch("cert_manager.reload._socat_command")
@patch("cert_manager.reload.os.path.exists", return_value=True)
def test_apply_ssl_cert_success(mock_exists, mock_socat):
    mock_socat.side_effect = [
        MagicMock(returncode=0, stdout="Updated", stderr=""),
        MagicMock(returncode=0, stdout="Committed", stderr=""),
    ]
    pem = b"-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n"
    assert apply_ssl_cert("/sock", "/etc/ssl/certs/haproxy.pem", pem, 0) is True
    assert mock_socat.call_count == 2
    set_call = mock_socat.call_args_list[0]
    assert "set ssl cert /etc/ssl/certs/haproxy.pem <<" in set_call[0][1]
    assert pem.decode("utf-8") in set_call[0][1]
    commit_call = mock_socat.call_args_list[1]
    assert commit_call[0][1] == "commit ssl cert /etc/ssl/certs/haproxy.pem\n"


@patch("cert_manager.reload._socat_command")
@patch("cert_manager.reload.os.path.exists", return_value=True)
def test_apply_ssl_cert_set_failure(mock_exists, mock_socat):
    mock_socat.side_effect = [
        MagicMock(returncode=1, stdout="", stderr="set failed"),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]
    assert apply_ssl_cert("/sock", "/crt.pem", b"pem", 0) is False


@patch("cert_manager.reload._socat_command")
@patch("cert_manager.reload.os.path.exists", return_value=True)
def test_apply_ssl_cert_commit_failure(mock_exists, mock_socat):
    mock_socat.side_effect = [
        MagicMock(returncode=0, stdout="", stderr=""),
        MagicMock(returncode=1, stdout="", stderr="commit failed"),
    ]
    assert apply_ssl_cert("/sock", "/crt.pem", b"pem", 0) is False


@patch("cert_manager.reload._socat_command", side_effect=FileNotFoundError)
@patch("cert_manager.reload.os.path.exists", return_value=True)
def test_apply_ssl_cert_socat_not_found(mock_exists, mock_socat):
    assert apply_ssl_cert("/sock", "/crt.pem", b"pem", 0) is False


@patch("cert_manager.reload._socat_command", side_effect=subprocess.TimeoutExpired("socat", 1))
@patch("cert_manager.reload.os.path.exists", return_value=True)
def test_apply_ssl_cert_timeout(mock_exists, mock_socat):
    assert apply_ssl_cert("/sock", "/crt.pem", b"pem", 0) is False


@patch("cert_manager.reload.time.monotonic")
@patch("cert_manager.reload.time.sleep")
@patch("cert_manager.reload.os.path.exists")
def test_wait_for_socket_eventually_found(mock_exists, mock_sleep, mock_monotonic):
    mock_monotonic.side_effect = [0.0, 1.0, 3.0]
    mock_exists.side_effect = [False, False, True]
    assert _wait_for_socket("/sock", 10) is True
    assert mock_sleep.call_count == 2


@patch("cert_manager.reload.time.monotonic")
@patch("cert_manager.reload.time.sleep")
@patch("cert_manager.reload.os.path.exists", return_value=False)
def test_wait_for_socket_timeout(mock_exists, mock_sleep, mock_monotonic):
    mock_monotonic.side_effect = [0.0, 5.0, 11.0]
    assert _wait_for_socket("/sock", 10) is False


@patch("cert_manager.reload.time.monotonic")
@patch("cert_manager.reload.os.path.exists", return_value=True)
def test_wait_for_socket_zero_wait(mock_exists, mock_monotonic):
    assert _wait_for_socket("/sock", 0) is True
    mock_monotonic.assert_not_called()


def test_cert_socket_timeout_constant():
    assert CERT_SOCKET_TIMEOUT_SEC == 60


@patch("cert_manager.reload.subprocess.run")
def test_socat_command_invokes_subprocess(mock_run):
    from cert_manager.reload import _socat_command

    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    result = _socat_command("/var/run/haproxy-stat/socket", "show info\n")
    assert result.returncode == 0
    mock_run.assert_called_once_with(
        ["socat", "STDIO", "UNIX-CONNECT:/var/run/haproxy-stat/socket"],
        input="show info\n",
        capture_output=True,
        text=True,
        timeout=CERT_SOCKET_TIMEOUT_SEC,
    )
