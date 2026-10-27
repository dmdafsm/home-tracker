"""Setări citite din variabilele de mediu (vezi .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = _env(name)
    return float(value) if value else default


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///data/hometracker.db"
    data_dir: Path = Path("data")
    profiles_dir: Path = Path("profiles")

    ntfy_url: str = ""
    ntfy_public_url: str = ""
    ntfy_token: str = ""
    ntfy_topic: str = "apartamente"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""

    interval_min_minutes: int = 30
    interval_max_minutes: int = 60
    pages_normal: int = 2
    pages_catchup: int = 6
    request_delay_min: float = 4.0
    request_delay_max: float = 9.0
    eur_ron: float = 4.97
    log_level: str = "INFO"

    # Reverificarea zilnică: anunțurile potrivite, nevăzute în căutare de atâtea ore, câte maximum pe rulare.
    recheck_after_hours: int = 24
    recheck_per_run: int = 10

    # Rezumatul zilnic pleacă la prima rulare de după această oră locală.
    summary_hour: int = 20
    timezone: str = "Europe/Bucharest"

    photos_per_listing: int = 5
    user_agent: str = field(
        default=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
        )
    )

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    @property
    def debug_dir(self) -> Path:
        return self.data_dir / "debug"


def load_settings() -> Settings:
    data_dir = Path(_env("DATA_DIR", "data"))
    return Settings(
        database_url=_env("DATABASE_URL", f"sqlite:///{data_dir / 'hometracker.db'}"),
        data_dir=data_dir,
        profiles_dir=Path(_env("PROFILES_DIR", "profiles")),
        ntfy_url=_env("NTFY_URL"),
        ntfy_public_url=_env("NTFY_PUBLIC_URL"),
        ntfy_token=_env("NTFY_TOKEN"),
        ntfy_topic=_env("NTFY_TOPIC", "apartamente"),
        smtp_host=_env("SMTP_HOST"),
        smtp_port=_env_int("SMTP_PORT", 587),
        smtp_user=_env("SMTP_USER"),
        smtp_password=_env("SMTP_PASSWORD"),
        email_from=_env("EMAIL_FROM"),
        email_to=_env("EMAIL_TO"),
        interval_min_minutes=_env_int("INTERVAL_MIN_MINUTES", 30),
        interval_max_minutes=_env_int("INTERVAL_MAX_MINUTES", 60),
        pages_normal=_env_int("PAGES_NORMAL", 2),
        pages_catchup=_env_int("PAGES_CATCHUP", 6),
        request_delay_min=_env_float("REQUEST_DELAY_MIN", 4.0),
        request_delay_max=_env_float("REQUEST_DELAY_MAX", 9.0),
        eur_ron=_env_float("EUR_RON", 4.97),
        log_level=_env("LOG_LEVEL", "INFO"),
        recheck_after_hours=_env_int("RECHECK_AFTER_HOURS", 24),
        recheck_per_run=_env_int("RECHECK_PER_RUN", 10),
        summary_hour=_env_int("SUMMARY_HOUR", 20),
        timezone=_env("TIMEZONE", "Europe/Bucharest"),
    )
