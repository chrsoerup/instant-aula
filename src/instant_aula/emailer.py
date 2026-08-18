"""SMTP delivery for the weekly digest and urgent alerts."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .config import Settings


def send(settings: Settings, subject: str, body: str, html: str | None = None) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = settings.smtp_to
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
