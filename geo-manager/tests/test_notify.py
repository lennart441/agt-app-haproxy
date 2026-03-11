"""Tests for geo_manager.notify."""
from unittest.mock import MagicMock, patch

import pytest

from geo_manager.config import Config
from geo_manager.notify import (
    notify_fail_open,
    notify_reload_failure,
    notify_validation_failure,
    send_failure_mail,
)


def _config(mail_enabled=True, mail_host="smtp.example.com", mail_to=None, mail_notify_validation_failure=True, mail_notify_reload_failure=True, mail_notify_fail_open=True):
    c = Config.from_env()
    c.mail_enabled = mail_enabled
    c.mail_host = mail_host
    c.mail_port = 587
    c.mail_use_tls = True
    c.mail_user = "user"
    c.mail_password = "pass"
    c.mail_from = "geo@example.com"
    c.mail_to = mail_to or ["admin@example.com"]
    c.mail_notify_validation_failure = mail_notify_validation_failure
    c.mail_notify_reload_failure = mail_notify_reload_failure
    c.mail_notify_fail_open = mail_notify_fail_open
    c.node_name = "agt-1"
    c.node_prio = 1
    return c


def test_send_failure_mail_disabled_returns_false():
    config = _config(mail_enabled=False)
    assert send_failure_mail(config, "Subj", "Body") is False


def test_send_failure_mail_no_host_returns_false():
    config = _config(mail_host="")
    assert send_failure_mail(config, "Subj", "Body") is False


def test_send_failure_mail_no_to_returns_false():
    config = _config(mail_to=[])
    assert send_failure_mail(config, "Subj", "Body") is False


@patch("geo_manager.notify.smtplib.SMTP")
def test_send_failure_mail_success(mock_smtp):
    config = _config()
    mock_server = MagicMock()
    mock_smtp.return_value.__enter__ = lambda self: self
    mock_smtp.return_value.__exit__ = lambda *a: None
    mock_smtp.return_value.starttls = MagicMock()
    mock_smtp.return_value.login = MagicMock()
    mock_smtp.return_value.sendmail = MagicMock()
    mock_smtp.return_value.quit = MagicMock()
    mock_smtp.return_value = mock_server
    result = send_failure_mail(config, "Subj", "Body")
    assert result is True
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("user", "pass")
    mock_server.sendmail.assert_called_once()
    mock_server.quit.assert_called_once()


@patch("geo_manager.notify.smtplib.SMTP")
def test_send_failure_mail_smtp_error_returns_false(mock_smtp):
    import smtplib
    config = _config()
    mock_smtp.return_value.starttls = MagicMock()
    mock_smtp.return_value.starttls.side_effect = smtplib.SMTPException("auth failed")
    result = send_failure_mail(config, "Subj", "Body")
    assert result is False


@patch("geo_manager.notify.smtplib.SMTP")
def test_send_failure_mail_os_error_returns_false(mock_smtp):
    config = _config()
    mock_smtp.side_effect = OSError("Network unreachable")
    result = send_failure_mail(config, "Subj", "Body")
    assert result is False


@patch("geo_manager.notify.smtplib.SMTP")
def test_send_failure_mail_unexpected_error_returns_false(mock_smtp):
    config = _config()
    mock_smtp.side_effect = RuntimeError("unexpected")
    result = send_failure_mail(config, "Subj", "Body")
    assert result is False


@patch("geo_manager.notify.smtplib.SMTP")
def test_send_failure_mail_no_tls(mock_smtp):
    config = _config()
    config.mail_use_tls = False
    mock_server = MagicMock()
    mock_smtp.return_value = mock_server
    result = send_failure_mail(config, "Subj", "Body")
    assert result is True
    mock_server.login.assert_called_once()
    mock_server.sendmail.assert_called_once()
    mock_server.quit.assert_called_once()


@patch("geo_manager.notify.send_failure_mail")
def test_notify_validation_failure_calls_send(mock_send):
    mock_send.return_value = True
    config = _config()
    notify_validation_failure(config, "size", "Size check failed")
    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert "[Geo-Manager] Validierung fehlgeschlagen (size)" in args[1]
    assert "Size check failed" in args[2]
    assert "agt-1" in args[2]


@patch("geo_manager.notify.send_failure_mail")
def test_notify_validation_failure_skipped_when_disabled(mock_send):
    config = _config(mail_notify_validation_failure=False)
    notify_validation_failure(config, "anchor", "detail")
    mock_send.assert_not_called()


@patch("geo_manager.notify.send_failure_mail")
def test_notify_reload_failure_calls_send(mock_send):
    mock_send.return_value = True
    config = _config()
    notify_reload_failure(config, "HAProxy reload failed")
    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert "HAProxy-Reload" in args[1]
    assert "HAProxy reload failed" in args[2]


@patch("geo_manager.notify.send_failure_mail")
def test_notify_reload_failure_skipped_when_disabled(mock_send):
    config = _config(mail_notify_reload_failure=False)
    notify_reload_failure(config, "detail")
    mock_send.assert_not_called()


@patch("geo_manager.notify.send_failure_mail")
def test_notify_fail_open_calls_send(mock_send):
    mock_send.return_value = True
    config = _config()
    notify_fail_open(config, "Geo-Liste fehlt (leer)")
    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert "Fail-Open" in args[1]
    assert "Geo-Liste fehlt" in args[2]
    assert "Sicherheitsrisiko" in args[2]


@patch("geo_manager.notify.send_failure_mail")
def test_notify_fail_open_skipped_when_disabled(mock_send):
    config = _config(mail_notify_fail_open=False)
    notify_fail_open(config, "detail")
    mock_send.assert_not_called()


@patch("geo_manager.notify.send_failure_mail")
def test_notify_validation_failure_does_not_raise_on_send_error(mock_send):
    mock_send.side_effect = RuntimeError("smtp down")
    config = _config()
    notify_validation_failure(config, "syntax", "detail")  # must not raise
    mock_send.assert_called_once()


@patch("geo_manager.notify.send_failure_mail")
def test_notify_reload_failure_does_not_raise_on_send_error(mock_send):
    mock_send.side_effect = OSError("network")
    config = _config()
    notify_reload_failure(config, "detail")  # must not raise
    mock_send.assert_called_once()


@patch("geo_manager.notify.send_failure_mail")
def test_notify_fail_open_does_not_raise_on_send_error(mock_send):
    mock_send.side_effect = Exception("unexpected")
    config = _config()
    notify_fail_open(config, "detail")  # must not raise
    mock_send.assert_called_once()
