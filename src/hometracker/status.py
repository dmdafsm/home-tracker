"""Starea colectării pe platformă, din `run_log`: ultima rulare, ultima reușită, problema de rezolvat."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import RunLog, as_utc, utcnow
from .profiles import Profile

# Câte rulări recente se arată pe platformă.
RECENT_RUNS = 5


@dataclass
class PlatformStatus:
    platform: str
    enabled: bool
    runs: list[RunLog]  # cele mai recente întâi
    last_ok: datetime | None
    problem: str | None

    @property
    def last(self) -> RunLog | None:
        return self.runs[0] if self.runs else None


def _problem(runs: list[RunLog], now: datetime, gap: timedelta) -> str | None:
    if not runs:
        return "încă nicio rulare"
    last = runs[0]
    if last.error:  # inclusiv un blocaj (HTTP 403, verificare, limită de cereri): se reîncearcă la rularea următoare
        return f"ultima rulare a eșuat: {last.error}"
    if last.found == 0:
        return "ultima rulare nu a găsit niciun anunț: probabil s-a schimbat pagina sau URL-ul de căutare"
    if now - as_utc(last.started_at) > gap:
        return "nicio rulare recentă: worker-ul e oprit sau calculatorul a dormit"
    return None


def collection_status(
    session: Session, profiles: list[Profile], settings: Settings, now: datetime | None = None
) -> list[PlatformStatus]:
    """O linie pentru fiecare platformă din profilurile active, în ordinea din profil."""
    now = now or utcnow()
    gap = timedelta(minutes=settings.interval_max_minutes * 2)
    platforms: dict[str, bool] = {}
    for profile in profiles:
        for name, source in profile.sources.items():
            platforms[name] = platforms.get(name, False) or source.enabled
    found = []
    for name, enabled in platforms.items():
        runs = session.scalars(
            select(RunLog).where(RunLog.platform == name).order_by(RunLog.started_at.desc()).limit(RECENT_RUNS)
        ).all()
        ok = session.scalar(
            select(RunLog.started_at)
            .where(RunLog.platform == name, RunLog.error.is_(None), RunLog.found > 0)
            .order_by(RunLog.started_at.desc())
            .limit(1)
        )
        problem = _problem(list(runs), now, gap) if enabled else None
        found.append(PlatformStatus(name, enabled, list(runs), ok, problem))
    return found
