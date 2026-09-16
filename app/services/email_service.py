"""
Outbound email. One message today: the code that gets you back into an account.

SMTP over STARTTLS, configured for a Gmail account with an app password
(`SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER` the address,
`SMTP_PASSWORD` the 16-character app password — not the account password,
which Google will refuse).

Optional at runtime: with no `SMTP_USER`/`SMTP_PASSWORD` set, `configured()` is
False, sends are logged and skipped, and every other part of the app is
unaffected. What that costs is password resets, so the forgot-password route
says so in the logs when it happens.
"""

import logging
import os
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "") or SMTP_USER
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "hum.log")
SMTP_TIMEOUT_SECONDS = 20


def configured() -> bool:
    return bool(SMTP_USER and SMTP_PASSWORD)


def send(to_email: str, subject: str, text_body: str, html_body: str | None = None) -> bool:
    """Send one message. Never raises — a bounced email is not a failed request."""
    if not configured():
        logger.warning(
            f"Email to {to_email} skipped: SMTP_USER/SMTP_PASSWORD are not set"
        )
        return False

    try:
        message = MIMEMultipart("alternative")
        message["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM))
        message["To"] = to_email
        message["Subject"] = subject
        message.attach(MIMEText(text_body, "plain", "utf-8"))
        if html_body:
            message.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_email], message.as_string())
        logger.info(f"Sent '{subject}' to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Email to {to_email} failed: {e}")
        return False


def send_in_background(to_email: str, subject: str, text_body: str, html_body: str | None = None) -> None:
    """Hand a message to a daemon thread and return immediately.

    Gmail's SMTP handshake takes a second or two on a good day. The
    forgot-password route answers the same way whether or not the address
    exists, so it must not sit and wait for the send either — the time it takes
    to reply would say what the reply refuses to.
    """
    threading.Thread(
        target=send, args=(to_email, subject, text_body, html_body), daemon=True
    ).start()


def send_password_reset(to_email: str, display_name: str, code: str, ttl_minutes: int) -> None:
    """The one message hum.log sends: a code to type back into the reset box."""
    greeting = display_name or "there"
    text_body = (
        f"Hi {greeting},\n\n"
        f"Someone asked to reset the password on your hum.log account.\n\n"
        f"Your code is: {code}\n\n"
        f"Type it into the reset box within {ttl_minutes} minutes. Yes, type it "
        f"— this is hum.log.\n\n"
        f"If this wasn't you, ignore this email. Nothing has changed, and the "
        f"code expires on its own.\n"
    )
    html_body = (
        f"<p>Hi {greeting},</p>"
        f"<p>Someone asked to reset the password on your hum.log account.</p>"
        f'<p style="font-size:28px;letter-spacing:6px;font-family:monospace">'
        f"<strong>{code}</strong></p>"
        f"<p>Type it into the reset box within {ttl_minutes} minutes. "
        f"Yes, type it &mdash; this is hum.log.</p>"
        f"<p>If this wasn't you, ignore this email. Nothing has changed, and "
        f"the code expires on its own.</p>"
    )
    send_in_background(to_email, "Your hum.log reset code", text_body, html_body)
