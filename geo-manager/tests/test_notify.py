"""Tests for geo_manager.notify."""
from unittest.mock import MagicMock, patch

import pytest

from geo_manager.config import Config
from geo_manager.notify import send_failure_mail


def _config(mail_enabled=True, mail_host="smtp.example.com", mail_to=None):
    c = Config.from_env()
    c.mail_enabled = mail_enabled
    c.mail_host = mail_host
    c.mail_port = 587
    c.mail_use_tls = True
    c.mail_user = "user"
    c.mail_password = "pass"
    c.mail_from = "geo@example.com"
    c.mail_to = mail_to or ["admin@example.com"]
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
