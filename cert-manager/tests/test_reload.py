"""Tests for cert_manager.reload."""

import subprocess
from unittest.mock import MagicMock, patch

from cert_manager.reload import (
    CERT_SOCKET_TIMEOUT_SEC,
    _PROBE_TIMEOUT_SEC,
    _connection_error_output,
    _socket_accepts_connections,
    _wait_for_socket,
    apply_ssl_cert,
)


def test_connection_error_output():
    assert _connection_error_output("E connect: Connection refused") is True
    assert _connection_error_output("No such file or directory") is True
    assert _connection_error_output("set failed") is False


@patch("cert_manager.reload.subprocess.run")
@patch("cert_manager.reload.os.path.exists", return_value=False)
def test_socket_accepts_connections_missing_file(mock_exists, mock_run):
    assert _socket_accepts_connections("/sock") is False
    mock_run.assert_not_called()


@patch("cert_manager.reload.subprocess.run")
@patch("cert_manager.reload.os.path.exists", return_value=True)
def test_socket_accepts_connections_success(mock_exists, mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="Version: 3.2", stderr="")
    assert _socket_accepts_connections("/sock") is True


@patch("cert_manager.reload.subprocess.run")
@patch("cert_manager.reload.os.path.exists", return_value=True)
def test_socket_accepts_connections_refused(mock_exists, mock_run):
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout="",
        stderr="socat E connect: Connection refused",
    )
    assert _socket_accepts_connections("/sock") is False


@patch("cert_manager.reload.subprocess.run", side_effect=FileNotFoundError)
@patch("cert_manager.reload.os.path.exists", return_value=True)
def test_socket_accepts_connections_socat_missing(mock_exists, mock_run):
    assert _socket_accepts_connections("/sock") is False


def test_apply_ssl_cert_skips_when_socket_path_empty():
    assert apply_ssl_cert("", "/etc/ssl/certs/haproxy.pem", b"pem") is True


@patch("cert_manager.reload._socket_accepts_connections", return_value=False)
@patch("cert_manager.reload.time.sleep")
def test_apply_ssl_cert_socket_unreachable_returns_true(mock_sleep, mock_ready):
    assert (
        apply_ssl_cert("/var/run/haproxy-stat/socket", "/crt.pem", b"pem", 0)
        is True
    )
    mock_sleep.assert_not_called()


@patch("cert_manager.reload._socat_command")
@patch("cert_manager.reload._socket_accepts_connections", return_value=True)
def test_apply_ssl_cert_success(mock_ready, mock_socat):
    mock_socat.side_effect = [
        MagicMock(returncode=0, stdout="Updated", stderr=""),
        MagicMock(returncode=0, stdout="Committed", stderr=""),
    ]
    pem = b"-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n"
    assert apply_ssl_cert("/sock", "/etc/ssl/certs/haproxy.pem", pem, 0) is True
    assert mock_socat.call_count == 2


@patch("cert_manager.reload._socat_command")
@patch("cert_manager.reload._socket_accepts_connections", return_value=True)
def test_apply_ssl_cert_set_failure(mock_ready, mock_socat):
    mock_socat.side_effect = [
        MagicMock(returncode=1, stdout="", stderr="invalid cert payload"),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]
    assert apply_ssl_cert("/sock", "/crt.pem", b"pem", 0) is False


@patch("cert_manager.reload._socat_command")
@patch("cert_manager.reload._socket_accepts_connections", return_value=True)
def test_apply_ssl_cert_connection_refused_returns_true(mock_ready, mock_socat):
    mock_socat.side_effect = [
        MagicMock(returncode=1, stdout="", stderr="Connection refused"),
        MagicMock(returncode=1, stdout="", stderr="Connection refused"),
    ]
    assert apply_ssl_cert("/sock", "/crt.pem", b"pem", 0) is True


@patch("cert_manager.reload._socat_command", side_effect=FileNotFoundError)
@patch("cert_manager.reload._socket_accepts_connections", return_value=True)
def test_apply_ssl_cert_socat_not_found(mock_ready, mock_socat):
    assert apply_ssl_cert("/sock", "/crt.pem", b"pem", 0) is False


@patch("cert_manager.reload._socat_command", side_effect=subprocess.TimeoutExpired("socat", 1))
@patch("cert_manager.reload._socket_accepts_connections", return_value=True)
def test_apply_ssl_cert_timeout(mock_ready, mock_socat):
    assert apply_ssl_cert("/sock", "/crt.pem", b"pem", 0) is False


@patch("cert_manager.reload.time.monotonic")
@patch("cert_manager.reload.time.sleep")
@patch("cert_manager.reload._socket_accepts_connections")
def test_wait_for_socket_eventually_found(mock_ready, mock_sleep, mock_monotonic):
    mock_monotonic.side_effect = [0.0, 1.0, 3.0]
    mock_ready.side_effect = [False, False, True]
    assert _wait_for_socket("/sock", 10) is True
    assert mock_sleep.call_count == 2


@patch("cert_manager.reload.time.monotonic")
@patch("cert_manager.reload.time.sleep")
@patch("cert_manager.reload._socket_accepts_connections", return_value=False)
def test_wait_for_socket_timeout(mock_ready, mock_sleep, mock_monotonic):
    mock_monotonic.side_effect = [0.0, 5.0, 11.0]
    assert _wait_for_socket("/sock", 10) is False


@patch("cert_manager.reload._socket_accepts_connections", return_value=True)
def test_wait_for_socket_zero_wait(mock_ready):
    assert _wait_for_socket("/sock", 0) is True


def test_timeout_constants():
    assert CERT_SOCKET_TIMEOUT_SEC == 60
    assert _PROBE_TIMEOUT_SEC == 5


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


@patch("cert_manager.reload.subprocess.run")
@patch("cert_manager.reload.os.path.exists", return_value=True)
def test_socket_accepts_connections_non_connection_error(mock_exists, mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="permission denied")
    assert _socket_accepts_connections("/sock") is True


@patch("cert_manager.reload.subprocess.run", side_effect=subprocess.TimeoutExpired("socat", 1))
@patch("cert_manager.reload.os.path.exists", return_value=True)
def test_socket_accepts_connections_probe_timeout(mock_exists, mock_run):
    assert _socket_accepts_connections("/sock") is False
