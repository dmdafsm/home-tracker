"""„Posibil același apartament”: sugestii pentru apartamente pe care deduplicarea nu le-a unit, și unirea manuală.

Deduplicarea automată cere poze comune sau un semnal de identitate, ca să nu unească apartamente
diferite din același bloc. Mai multe agenții care fotografiază fiecare apartamentul cu pozele ei
rămân astfel separate. Sugestiile arată perechile compatibile (același etaj și număr de camere,
suprafață și preț apropiate, aproape pe hartă), iar utilizatorul decide: „e același apartament”
le unește, „nu e același” nu le mai sugerează.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .dedup import NEAR_METERS, Candidate, contradiction, distance_m, find_republished_from
from .formatting import floor_label, format_price
from .models import DistinctProperties, LabelEvent, Listing, Property, PropertyLabel, utcnow

# Suprafața: aceeași toleranță ca pragul de contradicție din dedup (61 m² utili / 65 m² construiți).
AREA_TOLERANCE_M2 = 5
PRICE_TOLERANCE = 0.02


@dataclass(frozen=True)
class Suggestion:
    property_id: int
    listing: Listing  # anunțul activ cel mai ieftin al celuilalt apartament
    reasons: tuple[str, ...]
    rank: tuple = ()  # cele mai apropiate întâi: distanța (necunoscută la urmă), apoi diferența de preț


def _candidate(listing: Listing) -> Candidate:
    return Candidate(
        listing_id=listing.id,
        property_id=listing.property_id,
        area_m2=listing.area_m2,
        floor=listing.floor,
        rooms=listing.rooms,
        latitude=listing.latitude,
        longitude=listing.longitude,
        phone_hash=None,
        is_agency=listing.publisher_type == "agency",
        photos=(),
        shingles=frozenset(),
    )


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _known(values) -> set:
    return {value for value in values if value is not None}


def _compare(own: list[Listing], other: list[Listing]) -> tuple[tuple[str, ...], tuple] | None:
    """Motivele pentru care două apartamente par același, sau None dacă ceva nu se potrivește."""
    mine, theirs = [_candidate(item) for item in own], [_candidate(item) for item in other]
    if any(contradiction(a, b) for a in mine for b in theirs):
        return None
    floors, rooms = _known(item.floor for item in own + other), _known(item.rooms for item in own + other)
    if len(floors) != 1 or len(rooms) != 1:
        return None
    areas_a, areas_b = _known(item.area_m2 for item in own), _known(item.area_m2 for item in other)
    if not areas_a or not areas_b or min(abs(a - b) for a in areas_a for b in areas_b) > AREA_TOLERANCE_M2:
        return None
    prices_a = _known(item.price_eur for item in own if item.active)
    prices_b = _known(item.price_eur for item in other if item.active)
    if not prices_a or not prices_b:
        return None
    low_a, low_b = min(prices_a), min(prices_b)
    if abs(low_a - low_b) > PRICE_TOLERANCE * max(low_a, low_b):
        return None
    distances = [d for a in mine for b in theirs if (d := distance_m(a, b)) is not None]
    if distances and min(distances) > NEAR_METERS:
        return None

    areas = sorted(areas_a | areas_b)
    reasons = [
        floor_label(floors.pop()),
        f"{rooms.pop()} camere",
        " / ".join(f"{value:g}" for value in areas) + " mp",
        " / ".join(format_price(value) for value in sorted({low_a, low_b})),
        f"la {min(distances):.0f} m" if distances else "locație necunoscută la unul dintre ele",
    ]
    rank = (not distances, min(distances) if distances else 0.0, abs(low_a - low_b))
    return tuple(reasons), rank


def possible_duplicates(session: Session, property_ids=None) -> dict[int, list[Suggestion]]:
    """Sugestiile pentru fiecare apartament activ (sau doar pentru `property_ids`)."""
    listings = session.scalars(select(Listing).where(Listing.property_id.is_not(None))).all()
    by_property: dict[int, list[Listing]] = defaultdict(list)
    for listing in listings:
        by_property[listing.property_id].append(listing)
    active = {pid: items for pid, items in by_property.items() if any(item.active for item in items)}
    distinct = {(row.property_a, row.property_b) for row in session.scalars(select(DistinctProperties))}
    # Doar apartamentele cu același etaj și număr de camere pot fi comparate.
    buckets: dict[tuple, list[int]] = defaultdict(list)
    for pid, items in active.items():
        floors, rooms = _known(item.floor for item in items), _known(item.rooms for item in items)
        if len(floors) == 1 and len(rooms) == 1:
            buckets[(floors.pop(), rooms.pop())].append(pid)
    wanted = set(property_ids) if property_ids is not None else None
    found: dict[int, list[Suggestion]] = defaultdict(list)
    for members in buckets.values():
        for index, a in enumerate(members):
            for b in members[index + 1 :]:
                if wanted is not None and a not in wanted and b not in wanted:
                    continue
                if _pair(a, b) in distinct:
                    continue
                compared = _compare(active[a], active[b])
                if compared is None:
                    continue
                reasons, rank = compared
                for own, other in ((a, b), (b, a)):
                    cheapest = min(
                        (item for item in active[other] if item.active and item.price_eur is not None),
                        key=lambda item: item.price_eur,
                    )
                    found[own].append(Suggestion(other, cheapest, reasons, rank))
    return {pid: sorted(items, key=lambda item: item.rank) for pid, items in found.items()}


def mark_distinct(session: Session, a: int, b: int) -> None:
    """„Nu e același apartament”: perechea nu mai e sugerată."""
    if a == b:
        raise ValueError("același apartament")
    for pid in (a, b):
        if session.get(Property, pid) is None:
            raise LookupError(f"apartament inexistent: {pid}")
    low, high = _pair(a, b)
    exists = session.scalar(
        select(DistinctProperties).where(DistinctProperties.property_a == low, DistinctProperties.property_b == high)
    )
    if exists is None:
        session.add(DistinctProperties(property_a=low, property_b=high))
    session.add(LabelEvent(property_id=a, action="distinct"))
    session.flush()


def merge_properties(session: Session, a: int, b: int) -> int:
    """„E același apartament”: mută anunțurile și etichetele în apartamentul mai vechi."""
    if a == b:
        raise ValueError("același apartament")
    keep_id, drop_id = _pair(a, b)
    keep, drop = session.get(Property, keep_id), session.get(Property, drop_id)
    if keep is None or drop is None:
        raise LookupError("apartament inexistent")
    if not session.scalar(select(Listing.id).where(Listing.property_id == drop_id).limit(1)):
        raise LookupError("apartamentul a fost deja unit cu altul")
    moved = session.scalars(select(Listing).where(Listing.property_id == drop_id).order_by(Listing.first_seen)).all()
    for listing in moved:
        listing.property_id = keep_id
        listing.match_reason, listing.match_signals, listing.match_score = "manual", None, None
        listing.matched_listing_id, listing.shared_photos = None, 0
    kept_label, dropped_label = session.get(PropertyLabel, keep_id), session.get(PropertyLabel, drop_id)
    if dropped_label is not None:
        if kept_label is None:
            kept_label = PropertyLabel(property_id=keep_id, saved=False, hidden=dropped_label.hidden)
            session.add(kept_label)
        kept_label.saved = bool(kept_label.saved or dropped_label.saved)
        kept_label.verdict = kept_label.verdict or dropped_label.verdict
        kept_label.updated_at = utcnow()
        session.delete(dropped_label)
    for row in session.scalars(
        select(DistinctProperties).where(
            or_(DistinctProperties.property_a == drop_id, DistinctProperties.property_b == drop_id)
        )
    ):
        other = row.property_b if row.property_a == drop_id else row.property_a
        session.delete(row)
        if other != keep_id:
            session.flush()
            low, high = _pair(keep_id, other)
            if not session.scalar(
                select(DistinctProperties).where(
                    DistinctProperties.property_a == low, DistinctProperties.property_b == high
                )
            ):
                session.add(DistinctProperties(property_a=low, property_b=high))
    session.flush()
    session.expire(keep, ["listings"])
    for listing in moved:  # o agenție poate avea acum o republicare în apartamentul unit
        if listing.republished_from_id is None and (previous := find_republished_from(session, listing)):
            listing.republished_from_id = previous.id
    session.add(LabelEvent(property_id=keep_id, listing_id=moved[0].id if moved else None, action="merge"))
    # Rândul apartamentului unit rămâne, fără anunțuri: interfața și sugestiile pornesc de la anunțuri, deci nu
    # mai apare nicăieri. Nu-l ștergem, pentru că alte tabele pot avea chei străine spre el (de exemplu
    # tabelul `alert` rămas în bazele de date de dinainte de renunțarea la notificări).
    session.flush()
    return keep_id
