"""Istoricul unui apartament: cine îl publică, la ce preț, de când și de câte ori a fost republicat."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import Listing, ListingSnapshot, as_utc, utcnow
from .notify import PLATFORM_NAMES, floor_label, format_price

PUBLISHER_TYPES = {"owner": "proprietar", "agency": "agenție"}


SIGNAL_LABELS = {
    "phone": "același telefon",
    "area": "aceeași suprafață",
    "floor": "același etaj",
    "rooms": "același număr de camere",
    "near": "la sub 300 m",
}


def signal_label(signal: str) -> str:
    name, _, value = signal.partition(":")
    if name == "photos":
        return f"{value} poze comune" if value != "1" else "o poză comună"
    if name == "text":
        return f"text asemănător {float(value):.0%}"
    return SIGNAL_LABELS.get(name, name)


def match_label(listing: Listing) -> str | None:
    """De ce a fost legat anunțul de apartament, în cuvinte."""
    if listing.match_reason == "score" and listing.match_signals:
        signals = ", ".join(signal_label(s) for s in listing.match_signals.split(","))
        target = f" cu anunțul {listing.matched_listing_id}" if listing.matched_listing_id else ""
        return f"legat{target} (scor {listing.match_score:.2f}: {signals})"
    # Rânduri salvate înainte de scorul combinat.
    if listing.match_reason == "photos":
        return f"legat prin {listing.shared_photos} poze comune"
    if listing.match_reason == "photo+attributes":
        return "legat printr-o poză comună, aceeași suprafață și același etaj"
    return None


@dataclass
class PricePoint:
    seen_at: datetime
    listing: Listing
    price_eur: float


@dataclass
class PropertyHistory:
    property_id: int
    listings: list[Listing]
    prices: list[PricePoint] = field(default_factory=list)

    @property
    def first(self) -> Listing:
        """Prima apariție: de obicei sursa originală, adesea proprietarul."""
        return self.listings[0]

    @property
    def active(self) -> list[Listing]:
        return [listing for listing in self.listings if listing.active]

    @property
    def republications(self) -> list[Listing]:
        return [listing for listing in self.listings if listing.republished_from_id is not None]

    def days_on_market(self, now: datetime | None = None) -> int:
        """De la prima apariție până azi, sau până la ultima dispariție dacă nu mai e activ nicăieri."""
        start = as_utc(self.first.first_seen)
        if self.active:
            end = as_utc(now or utcnow())
        else:
            end = max(as_utc(listing.removed_at or listing.last_seen) for listing in self.listings)
        return max(0, (end - start).days)

    def price_range(self) -> tuple[float, float] | None:
        current = [listing.price_eur for listing in self.active or self.listings if listing.price_eur is not None]
        return (min(current), max(current)) if current else None


def property_history(session: Session, property_id: int) -> PropertyHistory | None:
    listings = session.scalars(
        select(Listing).where(Listing.property_id == property_id).order_by(Listing.first_seen, Listing.id)
    ).all()
    if not listings:
        return None
    history = PropertyHistory(property_id=property_id, listings=list(listings))
    by_id = {listing.id: listing for listing in listings}
    snapshots = session.scalars(
        select(ListingSnapshot)
        .where(ListingSnapshot.listing_id.in_(by_id), ListingSnapshot.price_eur.is_not(None))
        .order_by(ListingSnapshot.seen_at, ListingSnapshot.id)
    ).all()
    history.prices = [PricePoint(s.seen_at, by_id[s.listing_id], s.price_eur) for s in snapshots]
    return history


def find_property_id(session: Session, reference: str) -> int | None:
    """Apartamentul după id-ul lui sau după URL-ul unuia dintre anunțuri."""
    if reference.isdigit():
        return int(reference)
    return session.scalar(select(Listing.property_id).where(Listing.url == reference.split("?")[0]).limit(1))


def publisher_label(listing: Listing) -> str:
    label = PUBLISHER_TYPES.get(listing.publisher_type or "", "publicator necunoscut")
    return f"{label} ({listing.publisher_name})" if listing.publisher_name else label


def format_history(history: PropertyHistory, settings: Settings, now: datetime | None = None) -> str:
    zone = ZoneInfo(settings.timezone)

    def day(value: datetime | None) -> str:
        return as_utc(value).astimezone(zone).strftime("%d.%m.%Y") if value else "–"

    first = history.first
    lines = [f"Apartamentul {history.property_id}: {first.title}"]
    details = [floor_label(first.floor)]
    if first.area_m2:
        details.append(f"{first.area_m2:g} mp")
    if first.rooms:
        details.append(f"{first.rooms} cam.")
    lines.append(" · ".join(details))
    price_range = history.price_range()
    if price_range:
        low, high = price_range
        lines.append(f"Preț: {format_price(low)}" + (f" – {format_price(high)}" if high != low else ""))
    lines.append(
        f"Pe piață de {history.days_on_market(now)} zile, din {day(first.first_seen)} "
        f"(prima apariție: {PLATFORM_NAMES.get(first.platform, first.platform)}, {publisher_label(first)})"
    )
    lines.append(
        f"{len(history.listings)} anunțuri, {len(history.active)} active, {len(history.republications)} republicări"
    )

    lines.append("\nAnunțuri:")
    for listing in history.listings:
        state = "activ" if listing.active else f"dispărut {day(listing.removed_at)}"
        flags = [state]
        if listing.republished_from_id:
            flags.append(f"republicare a anunțului {listing.republished_from_id}")
        if label := match_label(listing):
            flags.append(label)
        lines.append(
            f"- [{listing.id}] {PLATFORM_NAMES.get(listing.platform, listing.platform)} · {publisher_label(listing)} · "
            f"{format_price(listing.price_eur)} · {day(listing.first_seen)} – {day(listing.last_seen)} · "
            + ", ".join(flags)
        )
        lines.append(f"  {listing.url}")

    if history.prices:
        lines.append("\nIstoricul prețului:")
        for point in history.prices:
            platform = PLATFORM_NAMES.get(point.listing.platform, point.listing.platform)
            lines.append(
                f"- {day(point.seen_at)}  {format_price(point.price_eur):>12}  {platform} [{point.listing.id}]"
            )
    return "\n".join(lines)
