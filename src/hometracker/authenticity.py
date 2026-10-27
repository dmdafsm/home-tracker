"""Scorul de autenticitate al unui apartament: 0–100, mereu împreună cu semnalele care l-au produs.

Tool-ul nu poate dovedi că un anunț e real; adună semnale și arată de ce un anunț e suspect, ca
utilizatorul să judece singur. Ponderile sunt fixe; etichetele „confirmat real” / „fals” din interfață
vor servi la calibrarea lor.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .history import PropertyHistory
from .models import Listing, Photo, as_utc
from .notify import PLATFORM_NAMES, format_price
from .publishers import PublisherStats, publisher_stats

PENALTIES = {"large": 30, "medium": 15, "small": 5}

MANY_PUBLISHERS = 3
BELOW_MEDIAN = 0.15
MIN_COMPARABLES = 5
RECYCLED_AFTER = timedelta(days=30)
SHOWCASE_DAYS = 90
SHOWCASE_REPUBLICATIONS = 2
MANY_PHOTOS = 5
PRICE_SPREAD = 0.01


@dataclass(frozen=True)
class Signal:
    code: str
    severity: str  # large / medium / small
    label: str  # ce indică, în cuvinte
    detail: str  # de ce, pentru acest apartament

    @property
    def penalty(self) -> int:
        return PENALTIES[self.severity]


@dataclass
class Assessment:
    signals: list[Signal] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # ce nu s-a putut verifica

    @property
    def score(self) -> int:
        return max(0, 100 - sum(signal.penalty for signal in self.signals))


@dataclass
class Context:
    """Datele comune tuturor apartamentelor, calculate o dată pe pagină sau pe rulare."""

    publishers: dict[int, PublisherStats]
    # (camere, parter sau nu) -> prețurile pe mp ale apartamentelor active, câte unul pe apartament
    price_per_m2: dict[tuple[int | None, bool], dict[int, float]]
    photo_counts: dict[int, int]

    @classmethod
    def build(cls, session: Session) -> Context:
        publishers = {stats.publisher.id: stats for stats in publisher_stats(session)}
        per_property: dict[int, list[Listing]] = defaultdict(list)
        for listing in session.scalars(
            select(Listing).where(Listing.property_id.is_not(None), Listing.active.is_(True))
        ):
            per_property[listing.property_id].append(listing)
        price_per_m2: dict[tuple[int | None, bool], dict[int, float]] = defaultdict(dict)
        for property_id, listings in per_property.items():
            priced = [item for item in listings if item.price_eur and item.area_m2]
            if priced:
                cheapest = min(priced, key=lambda item: item.price_eur)
                price_per_m2[_segment(cheapest)][property_id] = cheapest.price_eur / cheapest.area_m2
        photo_counts = dict(
            session.execute(
                select(Photo.listing_id, func.count(Photo.id))
                .where(Photo.phash.is_not(None))
                .group_by(Photo.listing_id)
            ).all()
        )
        return cls(publishers, price_per_m2, photo_counts)


def _segment(listing: Listing) -> tuple[int | None, bool]:
    """Tipologii comparabile: același număr de camere, parter sau etaj."""
    return listing.rooms, listing.floor in ("ground", "raised_ground", "basement")


def _platform(listing: Listing) -> str:
    return PLATFORM_NAMES.get(listing.platform, listing.platform)


def assess(history: PropertyHistory, context: Context) -> Assessment:
    result = Assessment()
    listings = history.listings
    active = history.active

    publishers = {listing.publisher_id for listing in listings if listing.publisher_id}
    if len(publishers) >= MANY_PUBLISHERS:
        result.signals.append(
            Signal(
                "many_publishers",
                "large",
                "anunț preluat de agenții",
                f"aceleași poze la {len(publishers)} publicatori diferiți",
            )
        )

    first = history.first
    agencies_after_first = [
        listing
        for listing in listings
        if listing.publisher_type == "agency"
        and listing.id != first.id
        and (listing.publisher_id is None or listing.publisher_id != first.publisher_id)
    ]
    if agencies_after_first:
        names = sorted({item.publisher_name or "o agenție" for item in agencies_after_first})
        original = {"owner": "proprietar", "agency": "altă agenție"}.get(
            first.publisher_type or "", "publicator necunoscut"
        )
        result.signals.append(
            Signal(
                "agency_not_first",
                "large",
                "preluat de la alt publicator",
                f"{', '.join(names)} a publicat după prima apariție ({_platform(first)}, {original})",
            )
        )

    reference = min(
        (item for item in active if item.price_eur and item.area_m2), key=lambda i: i.price_eur, default=None
    )
    if reference is not None:
        others = {
            pid: value
            for pid, value in context.price_per_m2.get(_segment(reference), {}).items()
            if pid != history.property_id
        }
        if len(others) >= MIN_COMPARABLES:
            typical = median(others.values())
            own = reference.price_eur / reference.area_m2
            if own < typical * (1 - BELOW_MEDIAN):
                result.signals.append(
                    Signal(
                        "below_median",
                        "large",
                        "preț-momeală",
                        f"{format_price(own)}/mp, cu {1 - own / typical:.0%} sub mediana de "
                        f"{format_price(typical)}/mp a {len(others)} apartamente comparabile",
                    )
                )
        else:
            result.notes.append(f"prețul nu poate fi comparat încă: doar {len(others)} apartamente asemănătoare")

    for publisher_id in publishers:
        stats = context.publishers.get(publisher_id)
        if stats and stats.disguised_agent:
            result.signals.append(
                Signal(
                    "disguised_agent",
                    "large",
                    "agent deghizat",
                    f"„proprietarul”{' ' + stats.publisher.name if stats.publisher.name else ''} "
                    f"apare la {len(stats.properties)} apartamente",
                )
            )

    gone = [item for item in listings if not item.active and item.removed_at is not None]
    for listing in active:
        earlier = [item for item in gone if as_utc(listing.first_seen) - as_utc(item.removed_at) >= RECYCLED_AFTER]
        if earlier:
            old = max(earlier, key=lambda item: as_utc(item.removed_at))
            days = (as_utc(listing.first_seen) - as_utc(old.removed_at)).days
            result.signals.append(
                Signal(
                    "recycled_photos",
                    "large",
                    "anunț reciclat",
                    f"pozele apăreau într-un anunț dispărut cu {days} zile înainte ({_platform(old)} #{old.id})",
                )
            )
            break

    prices = {round(point.price_eur) for point in history.prices}
    if (
        len(history.republications) >= SHOWCASE_REPUBLICATIONS
        and history.days_on_market() >= SHOWCASE_DAYS
        and len(prices) <= 1
    ):
        result.signals.append(
            Signal(
                "showcase",
                "medium",
                "anunț-vitrină",
                f"republicat de {len(history.republications)} ori în {history.days_on_market()} zile, la același preț",
            )
        )

    vague = [
        listing
        for listing in active
        if context.photo_counts.get(listing.id, 0) >= MANY_PHOTOS
        and (listing.floor is None or listing.area_m2 is None or not listing.location_text)
    ]
    if vague:
        missing = sorted(
            {
                name
                for item in vague
                for name, value in (("etaj", item.floor), ("suprafață", item.area_m2), ("adresă", item.location_text))
                if not value
            }
        )
        result.signals.append(
            Signal("vague", "small", "atragere de clienți", f"multe poze, dar lipsește: {', '.join(missing)}")
        )

    by_publisher: dict[int, list[Listing]] = defaultdict(list)
    for listing in active:
        if listing.publisher_id and listing.price_eur:
            by_publisher[listing.publisher_id].append(listing)
    for items in by_publisher.values():
        values = [item.price_eur for item in items]
        if len({item.platform for item in items}) > 1 and max(values) - min(values) > min(values) * PRICE_SPREAD:
            result.signals.append(
                Signal(
                    "inconsistent_prices",
                    "small",
                    "inconsecvență",
                    "același publicator, prețuri diferite: "
                    + ", ".join(f"{_platform(item)} {format_price(item.price_eur)}" for item in items),
                )
            )
            break
    return result
