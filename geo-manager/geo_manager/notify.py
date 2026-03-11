"""
Mail notifications via SMTP (e.g. mailcow).
Fetch failure, validation failure, reload failure, fail-open. Never raises – failures are logged only.
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)


def notify_validation_failure(config: "Config", reason: str, error_detail: str) -> None:
    """Send mail on validation failure (size/anchor/syntax) if enabled. Never raises."""
    if not getattr(config, "mail_notify_validation_failure", True):
        return
    subject = f"[Geo-Manager] Validierung fehlgeschlagen ({reason}) – {config.node_name}"
    body = (
        f"Knoten: {config.node_name} (Prio {config.node_prio})\n"
        f"Grund: {reason}\n"
        f"Details: {error_detail}\n"
        f"Die Geo-Maps wurden nicht aktiviert. Bitte prüfen."
    )
    try:
        send_failure_mail(config, subject, body)
    except Exception as e:
        logger.warning("Could not send validation failure mail: %s", e)


def notify_reload_failure(config: "Config", error_detail: str) -> None:
    """Send mail on HAProxy reload failure if enabled. Never raises."""
    if not getattr(config, "mail_notify_reload_failure", True):
        return
    subject = f"[Geo-Manager] HAProxy-Reload fehlgeschlagen – {config.node_name}"
    body = (
        f"Knoten: {config.node_name} (Prio {config.node_prio})\n"
        f"Fehler: {error_detail}\n"
        f"Die Maps wurden nicht geladen. Bitte HAProxy und Socket prüfen."
    )
    try:
        send_failure_mail(config, subject, body)
    except Exception as e:
        logger.warning("Could not send reload failure mail: %s", e)


def notify_fail_open(config: "Config", error_detail: str) -> None:
    """Send mail when fail-open was triggered (empty or too small geo list). Never raises."""
    if not getattr(config, "mail_notify_fail_open", True):
        return
    subject = f"[Geo-Manager] Fail-Open: Geo-Liste unbrauchbar – {config.node_name}"
    body = (
        f"Knoten: {config.node_name} (Prio {config.node_prio})\n"
        f"Alle Zugriffe wurden erlaubt (Sicherheitsrisiko).\n"
        f"Details: {error_detail}\n"
        f"Bitte GEO_SOURCE_URL und Geo-Daten prüfen."
    )
    try:
        send_failure_mail(config, subject, body)
    except Exception as e:
        logger.warning("Could not send fail-open mail: %s", e)


def send_failure_mail(config: "Config", subject: str, body: str) -> bool:
    """
    Send an email via SMTP (mailcow-compatible). Returns True on success.
    On any error: log and return False, never raise (container must not crash).
    """
    if not config.mail_enabled:
        logger.debug("Mail disabled; not sending")
        return False
    if not config.mail_host or not config.mail_to:
        logger.warning("Mail enabled but MAIL_HOST or MAIL_TO empty; skip send")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.mail_from or "geo-manager@localhost"
    msg["To"] = ", ".join(config.mail_to)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        if config.mail_use_tls:
            server = smtplib.SMTP(config.mail_host, config.mail_port, timeout=15)
            try:
                server.starttls()
                if config.mail_user and config.mail_password:
                    server.login(config.mail_user, config.mail_password)
                server.sendmail(
                    msg["From"],
                    config.mail_to,
                    msg.as_string(),
                )
            finally:
                server.quit()
        else:
            server = smtplib.SMTP(config.mail_host, config.mail_port, timeout=15)
            try:
                if config.mail_user and config.mail_password:
                    server.login(config.mail_user, config.mail_password)
                server.sendmail(
                    msg["From"],
                    config.mail_to,
                    msg.as_string(),
                )
            finally:
                server.quit()
        logger.info("Failure mail sent to %s", config.mail_to)
        return True
    except smtplib.SMTPException as e:
        logger.warning("SMTP error (mail not sent): %s", e)
        return False
    except OSError as e:
        logger.warning("Network/IO error sending mail: %s", e)
        return False
    except Exception as e:
        logger.warning("Unexpected error sending mail: %s", e)
        return False
