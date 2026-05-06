"""
Email Service
=============
Sends transactional emails via SMTP.
Configure SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM_EMAIL in .env.
"""

import asyncio
import smtplib
from email.mime.text import MIMEText

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("service.email")


def _send_sync(to_email: str, subject: str, body: str) -> None:
    settings = get_settings()
    if not settings.smtp_configured:
        logger.warning("smtp_not_configured", to=to_email, subject=subject)
        print(f"[EMAIL] To: {to_email} | Subject: {subject}\n{body}")
        return

    from_addr = settings.smtp_from_email or settings.smtp_user
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.sendmail(from_addr, [to_email], msg.as_string())


async def send_verification_email(to_email: str, code: str) -> None:
    subject = "ReelPush — Your verification code"
    body = (
        f"Your ReelPush verification code is:\n\n"
        f"  {code}\n\n"
        f"This code expires in 35 minutes. Do not share it with anyone."
    )
    await asyncio.to_thread(_send_sync, to_email, subject, body)
