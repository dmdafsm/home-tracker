"""Trimiterea notificărilor: ntfy (instant pe telefon) și, opțional, email."""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage

import httpx

from .config import Settings

log = logging.getLogger(__name__)

PLATFORM_NAMES = {
    "storia": "Storia",
    "olx": "OLX",
    "imobiliare": "Imobiliare.ro",
    "publi24": "Publi24",
    "lajumate": "Lajumate",
}
FLOOR_NAMES = {"basement": "demisol", "ground": "parter", "raised_ground": "parter înalt", "attic": "mansardă"}


@dataclass
class Message:
    title: str
    body: str
    url: str | None = None
    image_url: str | None = None
    tags: list[str] = field(default_factory=list)
    priority: int = 3  # 1–5, ca în ntfy


def floor_label(floor: str | None) -> str:
    if floor is None:
        return "etaj necunoscut"
    return FLOOR_NAMES.get(floor, f"etaj {floor}")


def format_price(value: float | None) -> str:
    if value is None:
        return "preț necunoscut"
    return f"{value:,.0f} €".replace(",", ".")


class Notifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(
        self, message: Message, channels: list[str], *, ntfy_topic: str | None = None, email_to: str | None = None
    ) -> list[str]:
        """Trimite pe canalele cerute și întoarce lista celor care au reușit.

        `ntfy_topic` și `email_to` sunt destinațiile unui profil; lipsă = cele din setări.
        """
        delivered = []
        topic = ntfy_topic or self.settings.ntfy_topic
        recipient = email_to or self.settings.email_to
        for channel in channels:
            try:
                if channel == "ntfy" and self.settings.ntfy_url:
                    self._ntfy(message, topic)
                    delivered.append(channel)
                elif channel == "email" and self.settings.smtp_host and recipient:
                    self._email(message, recipient)
                    delivered.append(channel)
            except (httpx.HTTPError, smtplib.SMTPException, OSError) as exc:
                log.error("notificarea pe %s a eșuat: %s", channel, exc)
        if not delivered:
            log.warning("nicio notificare livrată (canale configurate?): %s", message.title)
        return delivered

    def _ntfy(self, message: Message, topic: str) -> None:
        # Publicare JSON: suportă diacritice în titlu, spre deosebire de headerele HTTP.
        payload: dict = {
            "topic": topic,
            "title": message.title,
            "message": message.body,
            "priority": message.priority,
            "tags": message.tags,
        }
        if message.url:
            payload["click"] = message.url
            payload["actions"] = [{"action": "view", "label": "Deschide anunțul", "url": message.url}]
        if message.image_url:
            payload["attach"] = message.image_url
        headers = {"Authorization": f"Bearer {self.settings.ntfy_token}"} if self.settings.ntfy_token else {}
        response = httpx.post(self.settings.ntfy_url.rstrip("/") + "/", json=payload, headers=headers, timeout=15)
        response.raise_for_status()

    def _email(self, message: Message, recipient: str) -> None:
        email = EmailMessage()
        email["Subject"] = message.title
        email["From"] = self.settings.email_from or self.settings.smtp_user
        email["To"] = recipient
        body = message.body + (f"\n\n{message.url}" if message.url else "")
        email.set_content(body)
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if self.settings.smtp_user:
                smtp.login(self.settings.smtp_user, self.settings.smtp_password)
            smtp.send_message(email)
