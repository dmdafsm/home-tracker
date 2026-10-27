"""Gruparea anunțurilor pe apartamente fizice, după poze, și detectarea republicărilor."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Listing, Photo, Property
from .photos import hamming, is_informative

# Distanța Hamming maximă (din 64 de biți) pentru „aceeași poză”.
PHASH_THRESHOLD = 8
# Câte poze comune sunt necesare. O singură poză comună (de obicei fațada blocului)
# nu ajunge: agențiile refolosesc poze de exterior pentru apartamente diferite din același bloc.
MIN_SHARED_PHOTOS = 2


@dataclass(frozen=True)
class PropertyMatch:
    property_id: int
    shared_photos: int
    reason: str  # "photos" sau "photo+attributes"


def find_matching_property(session: Session, listing: Listing) -> PropertyMatch | None:
    hashes = [p.phash for p in listing.photos if p.phash and is_informative(p.phash)]
    if not hashes:
        return None
    rows = session.execute(
        select(Photo.phash, Listing.property_id, Listing.area_m2, Listing.floor)
        .join(Listing, Photo.listing_id == Listing.id)
        .where(Listing.id != listing.id, Listing.property_id.is_not(None), Photo.phash.is_not(None))
    ).all()

    shared: Counter[int] = Counter()
    attributes: dict[int, tuple[float | None, str | None]] = {}
    for own in hashes:
        hit_properties = {
            prop_id for other, prop_id, *_ in rows if is_informative(other) and hamming(own, other) <= PHASH_THRESHOLD
        }
        shared.update(hit_properties)
    for _, prop_id, area, floor in rows:
        attributes.setdefault(prop_id, (area, floor))

    for prop_id, count in shared.most_common():
        if count >= MIN_SHARED_PHOTOS:
            return PropertyMatch(prop_id, count, "photos")
        # O singură poză comună e acceptată doar dacă suprafața și etajul confirmă.
        area, floor = attributes.get(prop_id, (None, None))
        same_area = area is not None and listing.area_m2 is not None and abs(area - listing.area_m2) <= 2
        same_floor = floor is not None and floor == listing.floor
        if count == 1 and same_area and same_floor:
            return PropertyMatch(prop_id, count, "photo+attributes")
    return None


def publisher_key(listing: Listing) -> str | None:
    """Cine a publicat anunțul: telefonul (hash) sau numele normalizat; None dacă nu se știe."""
    if listing.phone_hash:
        return f"phone:{listing.phone_hash}"
    name = " ".join((listing.publisher_name or "").lower().split())
    return f"name:{name}" if name else None


def find_republished_from(session: Session, listing: Listing) -> Listing | None:
    """Anunțul mai vechi pe care acesta îl republică: același apartament, platformă și publicator.

    Fără publicator cunoscut nu marcăm nimic: două anunțuri anonime pentru același apartament pot
    veni de la oameni diferiți (proprietarul și o agenție care i-a preluat pozele).
    """
    key = publisher_key(listing)
    if key is None or listing.property_id is None:
        return None
    earlier = session.scalars(
        select(Listing)
        .where(
            Listing.property_id == listing.property_id,
            Listing.platform == listing.platform,
            Listing.id != listing.id,
            Listing.first_seen <= listing.first_seen,
        )
        .order_by(Listing.first_seen.desc(), Listing.id.desc())
    ).all()
    return next((other for other in earlier if publisher_key(other) == key), None)


def assign_property(session: Session, listing: Listing) -> Property:
    """Leagă anunțul de un apartament existent sau creează unul nou."""
    if listing.property_id is not None:
        return listing.property  # type: ignore[return-value]
    found = find_matching_property(session, listing)
    if found is not None:
        prop = session.get(Property, found.property_id)
        listing.match_reason, listing.shared_photos = found.reason, found.shared_photos
    else:
        prop = Property()
        session.add(prop)
        session.flush()
        listing.match_reason, listing.shared_photos = "new", 0
    listing.property = prop
    session.flush()
    if found is not None:
        previous = find_republished_from(session, listing)
        listing.republished_from_id = previous.id if previous else None
    return prop  # type: ignore[return-value]
