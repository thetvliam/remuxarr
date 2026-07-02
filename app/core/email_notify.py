"""
Email notifications for job failures.

Uses only Python's stdlib smtplib + email — no extra dependencies.

The consecutive-failure circuit breaker itself lives in worker.py
(_load_email_notify_data), which decides WHETHER to send and WHAT kind of
email. This module only knows how to actually send: a normal per-failure
email, the one combined "notifications paused" email, or a test message.

All sends are best-effort — a failure to send (bad SMTP config, network
issue, etc.) is logged but never raised back to the worker, consistent
with how the rest of the notification integrations (Sonarr, Radarr, Plex)
already treat external notification as something that should never affect
a job's own recorded success or failure.
"""

import logging
import smtplib
from email.mime.text import MIMEText
from email.utils import formatdate

logger = logging.getLogger(__name__)


def _send(cfg: dict, subject: str, body: str) -> None:
    """
    Raises on failure — callers in this module catch and log; the one
    exception is test_email_connection(), which wants the raw error to
    show the user exactly what went wrong.
    """
    host       = (cfg.get("email_smtp_host") or "").strip()
    port       = int(cfg.get("email_smtp_port") or 587)
    encryption = cfg.get("email_encryption", "starttls")
    username   = cfg.get("email_username") or ""
    password   = cfg.get("email_password") or ""
    from_addr  = (cfg.get("email_from") or username or "").strip()
    recipients = [r.strip() for r in cfg.get("email_recipients", []) if r.strip()]

    if not host:
        raise ValueError("SMTP host not configured")
    if not recipients:
        raise ValueError("No recipient addresses configured")
    if not from_addr:
        raise ValueError("From address not configured")

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = ", ".join(recipients)
    msg["Date"]    = formatdate(localtime=True)

    smtp_cls = smtplib.SMTP_SSL if encryption == "ssl" else smtplib.SMTP
    smtp = smtp_cls(host, port, timeout=15)
    try:
        if encryption == "starttls":
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.sendmail(from_addr, recipients, msg.as_string())
    finally:
        try:
            smtp.quit()
        except Exception:
            pass


def send_failure_email(cfg: dict, filename: str, error: str | None, count: int) -> None:
    """Individual job-failure email. Best-effort — logs on failure, never raises."""
    subject = f"Remuxarr: {filename} failed"
    body = (
        f"A job failed while processing:\n\n"
        f"  File:  {filename}\n"
        f"  Error: {error or '(no error message captured)'}\n\n"
        f"This is consecutive failure #{count}. If failures continue, "
        f"notifications will pause once the configured threshold is "
        f"reached, to avoid flooding this inbox — they resume "
        f"automatically as soon as a job succeeds."
    )
    try:
        _send(cfg, subject, body)
    except Exception:
        logger.exception("Failed to send failure-notification email for %s", filename)


def send_breaker_tripped_email(cfg: dict, count: int) -> None:
    """
    The one combined warning email sent when the consecutive-failure
    threshold is crossed. After this, no further emails are sent until a
    job succeeds. Best-effort — logs on failure, never raises.
    """
    subject = "Remuxarr: failure notifications paused"
    body = (
        f"Remuxarr has had {count} consecutive job failures.\n\n"
        f"To avoid flooding this inbox, no further individual failure "
        f"emails will be sent. Notifications resume automatically as soon "
        f"as a job completes successfully.\n\n"
        f"Check the Remuxarr History panel (Failed tab) and the "
        f"Application Logs in Settings to diagnose the underlying issue — "
        f"this pattern usually means a recent settings change or path "
        f"problem is affecting every file, not that the files themselves "
        f"are at fault."
    )
    try:
        _send(cfg, subject, body)
    except Exception:
        logger.exception("Failed to send breaker-tripped notification email")


def test_email_connection(cfg: dict) -> dict:
    """
    Sends an actual test email and reports success or failure.
    There's no lightweight "ping" for raw SMTP the way there is for the
    Sonarr/Radarr/Plex HTTP APIs — actually sending is the only way to
    validate host, port, encryption, auth, and recipient address together.
    """
    try:
        _send(
            cfg,
            "Remuxarr: test email",
            "This is a test email from Remuxarr to confirm your SMTP "
            "settings are configured correctly.\n\n"
            "If you received this, failure notifications are ready to go.",
        )
        return {"success": True, "message": "Test email sent"}
    except Exception as e:
        return {"success": False, "error": str(e)}
