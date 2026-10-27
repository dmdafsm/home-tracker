"""Rezumatul zilnic: anunțuri noi slab potrivite, anunțuri dispărute și golurile din colectare."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Alert, Listing, RunLog, SummaryLog, utcnow
from .notify import PLATFORM_NAMES, Message, Notifier, floor_label, format_price
from .pipeline import listing_to_data
from .profiles import MatchResult, Profile, match

log = logging.getLogger(__name__)

DEFAULT_PERIOD = timedelta(hours=24)
# Un rezumat nu acoperă mai mult de atât, chiar dacă ultimul a fost trimis demult.
MAX_PERIOD = timedelta(days=7)
# ntfy taie mesajele mai lungi de 4096 de octeți.
NTFY_LIMIT = 3500


def as_utc(value: datetime) -> datetime:
    """SQLite întoarce datele fără fus orar; în baza de date sunt mereu UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass
class Summary:
    profile: Profile
    start: datetime
    end: datetime
    weak: list[tuple[Listing, MatchResult]] = field(default_factory=list)
    removed: list[Listing] = field(default_factory=list)
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)
    alerts: Counter[str] = field(default_factory=Counter)


def build_summary(session: Session, profile: Profile, start: datetime, end: datetime, settings: Settings) -> Summary:
    summary = Summary(profile=profile, start=start, end=end)
    alerted = set(
        session.scalars(select(Alert.property_id).where(Alert.profile == profile.name, Alert.kind == "new")).all()
    )

    # Noi în perioadă, care trec de filtrele profilului, dar n-au primit alertă instant (scor sub prag).
    seen_properties: set[int] = set()
    new_listings = session.scalars(
        select(Listing)
        .where(Listing.first_seen >= start, Listing.first_seen < end, Listing.property_id.is_not(None))
        .order_by(Listing.first_seen)
    ).all()
    for listing in new_listings:
        if listing.property_id in alerted or listing.property_id in seen_properties:
            continue
        result = match(profile, listing_to_data(listing))
        if result.passed:
            seen_properties.add(listing.property_id)
            summary.weak.append((listing, result))
    summary.weak.sort(key=lambda pair: -pair[1].score)

    # Dispărute în perioadă: doar cele relevante pentru profil (alertate sau care încă trec de filtre).
    removed = session.scalars(
        select(Listing)
        .where(Listing.removed_at >= start, Listing.removed_at < end, Listing.property_id.is_not(None))
        .order_by(Listing.removed_at)
    ).all()
    summary.removed = [
        listing
        for listing in removed
        if listing.property_id in alerted or match(profile, listing_to_data(listing)).passed
    ]

    summary.gaps = collection_gaps(session, profile, start, end, settings)
    summary.alerts = Counter(
        dict(
            session.execute(
                select(Alert.kind, func.count(Alert.id))
                .where(Alert.profile == profile.name, Alert.sent_at >= start, Alert.sent_at < end)
                .group_by(Alert.kind)
            ).all()
        )
    )
    return summary


def collection_gaps(
    session: Session, profile: Profile, start: datetime, end: datetime, settings: Settings
) -> list[tuple[datetime, datetime]]:
    """Intervalele fără nicio rulare, mai lungi de două intervale de colectare (calculator oprit sau în sleep)."""
    limit = timedelta(minutes=settings.interval_max_minutes * 2)
    runs = [
        as_utc(value)
        for value in session.scalars(
            select(RunLog.started_at)
            .where(RunLog.profile == profile.name, RunLog.started_at >= start, RunLog.started_at < end)
            .order_by(RunLog.started_at)
        ).all()
    ]
    points = [as_utc(start), *runs, as_utc(end)]
    return [(a, b) for a, b in pairwise(points) if b - a > limit]


def format_summary(summary: Summary, settings: Settings) -> Message:
    zone = ZoneInfo(settings.timezone)

    def local(value: datetime) -> str:
        return as_utc(value).astimezone(zone).strftime("%d.%m %H:%M")

    def line(listing: Listing, extra: str = "") -> str:
        parts = [format_price(listing.price_eur), floor_label(listing.floor)]
        if listing.area_m2:
            parts.append(f"{listing.area_m2:g} mp")
        parts.append(PLATFORM_NAMES.get(listing.platform, listing.platform))
        if extra:
            parts.append(extra)
        return f"- {' · '.join(parts)}\n  {listing.title[:90]}\n  {listing.url}"

    sections = [f"Perioada: {local(summary.start)} – {local(summary.end)}"]
    sent = summary.alerts.get("new", 0), summary.alerts.get("price_drop", 0)
    sections.append(f"Alerte instant trimise: {sent[0]} apartamente noi, {sent[1]} scăderi de preț.")
    if summary.weak:
        sections.append(
            f"Noi, sub pragul de alertă ({len(summary.weak)}):\n"
            + "\n".join(line(listing, f"scor {result.score:.0f}") for listing, result in summary.weak)
        )
    if summary.removed:
        sections.append(
            f"Dispărute, posibil vândute ({len(summary.removed)}):\n"
            + "\n".join(line(listing) for listing in summary.removed)
        )
    if summary.gaps:
        sections.append(
            "Goluri în colectare (anunțurile apărute și dispărute atunci se pot pierde):\n"
            + "\n".join(f"- {local(a)} – {local(b)}" for a, b in summary.gaps)
        )
    if not (summary.weak or summary.removed):
        sections.append("Nimic altceva nou.")
    counts = f"{len(summary.weak)} noi slab potrivite, {len(summary.removed)} dispărute"
    return Message(
        title=f"Rezumat zilnic · {summary.profile.name} · {counts}",
        body="\n\n".join(sections),
        tags=["newspaper"],
        priority=2,
    )


def due_period(
    session: Session, profile: Profile, now: datetime, settings: Settings
) -> tuple[datetime, datetime] | None:
    """Perioada rezumatului de trimis acum, sau None dacă nu e încă ora sau a fost deja trimis azi."""
    zone = ZoneInfo(settings.timezone)
    local_now = as_utc(now).astimezone(zone)
    if local_now.hour < settings.summary_hour:
        return None
    last = session.scalar(
        select(SummaryLog).where(SummaryLog.profile == profile.name).order_by(SummaryLog.period_end.desc()).limit(1)
    )
    if last is not None and as_utc(last.sent_at).astimezone(zone).date() == local_now.date():
        return None
    end = as_utc(now)
    start = as_utc(last.period_end) if last is not None else end - DEFAULT_PERIOD
    return max(start, end - MAX_PERIOD), end


def send_summary(
    sessions: sessionmaker, profile: Profile, notifier: Notifier, settings: Settings, start: datetime, end: datetime
) -> bool:
    with sessions() as session:
        message = format_summary(build_summary(session, profile, start, end, settings), settings)
        channels = profile.notify.summary_channels
        if "ntfy" in channels and len(message.body.encode()) > NTFY_LIMIT:
            # Emailul primește tot textul; ntfy doar începutul.
            delivered = notifier.send(message, [c for c in channels if c != "ntfy"])
            body = message.body.encode()[:NTFY_LIMIT].decode(errors="ignore") + "\n…"
            delivered += notifier.send(replace(message, body=body), ["ntfy"])
        else:
            delivered = notifier.send(message, channels)
        if not delivered:
            log.warning("rezumatul pentru %s nu a putut fi trimis; reîncerc la rularea următoare", profile.name)
            return False
        session.add(SummaryLog(profile=profile.name, period_start=start, period_end=end, channels=",".join(delivered)))
        session.commit()
        log.info("rezumat trimis pentru %s pe %s", profile.name, ", ".join(delivered))
        return True


def send_due_summaries(
    sessions: sessionmaker, profiles: list[Profile], notifier: Notifier, settings: Settings, now: datetime | None = None
) -> int:
    now = now or utcnow()
    sent = 0
    for profile in profiles:
        if not profile.notify.summary_channels:
            continue
        with sessions() as session:
            period = due_period(session, profile, now, settings)
        if period and send_summary(sessions, profile, notifier, settings, *period):
            sent += 1
    return sent
