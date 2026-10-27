"""Gruparea anunțurilor pe apartamente fizice, după poze (MVP)."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Listing, Photo, Property
from .photos import hamming, is_informative

# Distanța Hamming maximă (din 64 de biți) pentru „aceeași poză”.
PHASH_THRESHOLD = 8
# Câte poze comune sunt necesare. O singură poză comună (de obicei fațada blocului)
# nu ajunge: agențiile refolosesc poze de exterior pentru apartamente diferite din același bloc.
MIN_SHARED_PHOTOS = 2


def find_matching_property(session: Session, listing: Listing) -> int | None:
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
            return prop_id
        # O singură poză comună e acceptată doar dacă suprafața și etajul confirmă.
        area, floor = attributes.get(prop_id, (None, None))
        same_area = area is not None and listing.area_m2 is not None and abs(area - listing.area_m2) <= 2
        same_floor = floor is not None and floor == listing.floor
        if count == 1 and same_area and same_floor:
            return prop_id
    return None


def assign_property(session: Session, listing: Listing) -> Property:
    """Leagă anunțul de un apartament existent sau creează unul nou."""
    if listing.property_id is not None:
        return listing.property  # type: ignore[return-value]
    prop_id = find_matching_property(session, listing)
    if prop_id is not None:
        prop = session.get(Property, prop_id)
    else:
        prop = Property()
        session.add(prop)
        session.flush()
    listing.property = prop
    session.flush()
    return prop  # type: ignore[return-value]
