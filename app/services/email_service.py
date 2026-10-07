import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class SentEmail:
    to: str
    subject: str
    body: str


# Messages sent with EMAIL_BACKEND=memory, newest last. Tests read it.
OUTBOX: list[SentEmail] = []


class EmailService:
    """
    Sends transactional email.

      smtp     deliver through SMTP_HOST
      console  log the message, links included (development default)
      memory   append to OUTBOX (tests)
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def link(self, path: str, token: str) -> str:
        return f"{self.settings.frontend_url.rstrip('/')}{path}?token={token}"

    async def send(self, to: str, subject: str, body: str) -> None:
        backend = self.settings.email_backend
        if backend == "memory":
            OUTBOX.append(SentEmail(to, subject, body))
            return
        if backend == "console":
            logger.warning("Email not sent (EMAIL_BACKEND=console)\nTo: %s\nSubject: %s\n\n%s", to, subject, body)
            return
        try:
            await asyncio.to_thread(self._send_smtp, to, subject, body)
        except Exception:
            # Callers answer the same either way (no account enumeration);
            # the failure is for operators.
            logger.exception("Sending email to %s failed", to)

    def _send_smtp(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.settings.email_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=15) as smtp:
            if self.settings.smtp_starttls:
                smtp.starttls()
            if self.settings.smtp_username:
                smtp.login(self.settings.smtp_username, self.settings.smtp_password or "")
            smtp.send_message(message)
