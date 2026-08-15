import asyncio
import logging
import smtplib
from email.message import EmailMessage

from common.config import NotifierSettings

logger = logging.getLogger(__name__)


class EmailClient:
    """Thin SMTP wrapper for the weekly business-performance summary —
    email's counterpart to `TelegramClient`. Telegram stays limited to
    buy/sell trade actions plus safety alerts (PROJECT.md Section 4); the
    weekly rollup is low-frequency enough to belong in an inbox instead,
    read on its own schedule rather than mixed into the trade-alert feed.

    Plain text only, same rationale as `TelegramClient`: keeps the two
    notification paths consistent and avoids an HTML-email dependency for
    a once-a-week message.

    `smtplib` is blocking, so the actual send runs in a thread
    (`asyncio.to_thread`) to avoid stalling the notifier's event loop —
    the other polling loops (audit events, Telegram commands) keep running
    normally during the few hundred ms an SMTP round-trip takes.
    """

    def __init__(self, settings: NotifierSettings | None = None) -> None:
        self._settings = settings or NotifierSettings()

    def _send_sync(self, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._settings.email_from or self._settings.smtp_username
        message["To"] = self._settings.email_to
        message.set_content(body)

        with smtplib.SMTP(self._settings.smtp_host, self._settings.smtp_port, timeout=15.0) as smtp:
            smtp.starttls()
            if self._settings.smtp_username:
                smtp.login(self._settings.smtp_username, self._settings.smtp_password)
            smtp.send_message(message)

    async def send_email(self, subject: str, body: str) -> bool:
        if not self._settings.smtp_host or not self._settings.email_to:
            # Same "never blocks or delays" posture as Telegram (PROJECT.md
            # Section 9.4): an unconfigured mailer is a no-op, not a crash,
            # so a deploy without SMTP secrets set yet doesn't take down the
            # weekly loop (or the process — NotifierSettings has no
            # required fields).
            logger.warning("email_not_configured", extra={"subject": subject})
            return False
        try:
            await asyncio.to_thread(self._send_sync, subject, body)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            logger.warning("email_send_failed", extra={"error": str(exc), "subject": subject})
            return False
