"""Gruparea anunțurilor pe apartamente fizice (poze, text, telefon, atribute) și detectarea republicărilor."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Listing, Photo, Property
from .parsing import normalize
from .photos import hamming, is_informative

# Aceeași poză: pHash aproape identic, sau pHash puțin mai departe dar confirmat de dHash
# (poze redimensionate, recomprimate sau cu un watermark mic).
PHASH_SAME = 8
PHASH_NEAR = 12
DHASH_NEAR = 8

# Text: shingles de câte 3 cuvinte, după normalizare. Shingles prezente în multe anunțuri sunt
# textul-șablon al unei agenții („contactați-ne”, „comision 0%”) și nu spun nimic despre apartament.
SHINGLE_SIZE = 3
MIN_SHINGLES = 10
COMMON_SHINGLE_LISTINGS = 5
TEXT_STRONG = 0.5
TEXT_WEAK = 0.3

AREA_TOLERANCE_M2 = 2
AREA_CONTRADICTION_M2 = 5
NEAR_METERS = 300
FAR_METERS = 1000

# Ponderi fixe (planul: se pot învăța mai târziu din etichetele din interfață). Pragul e ales așa încât
# regulile vechi să rămână valabile: 2 poze comune ajung; o poză comună cere aceeași suprafață și același
# etaj. Fără poze comune, textul și atributele nu ajung singure (vezi IDENTITY_SIGNALS).
WEIGHTS = {
    "photos_many": 0.75,
    "photos_one": 0.45,
    "text_strong": 0.4,
    "text_weak": 0.25,
    "phone": 0.2,
    "area": 0.15,
    "floor": 0.15,
    "rooms": 0.05,
    "near": 0.05,
}
MATCH_THRESHOLD = 0.75
# Fără poze comune, o potrivire are nevoie și de un semnal care identifică apartamentul: același telefon
# sau coordonate apropiate. Textul-șablon al unei agenții e recunoscut ca șablon abia după câteva anunțuri
# (COMMON_SHINGLE_LISTINGS), iar până atunci text + suprafață + etaj + camere ar uni apartamente diferite.
IDENTITY_SIGNALS = {"phone", "near"}
# Telefonul identifică apartamentul doar dacă nu e al unei agenții: o agenție (sau un agent deghizat în
# proprietar) folosește același număr pentru multe apartamente.
PHONE_MAX_PROPERTIES = 1


@dataclass(frozen=True)
class Candidate:
    listing_id: int
    property_id: int | None
    area_m2: float | None
    floor: str | None
    rooms: int | None
    latitude: float | None
    longitude: float | None
    phone_hash: str | None
    is_agency: bool
    photos: tuple[tuple[str, str | None], ...]
    shingles: frozenset[str]


@dataclass(frozen=True)
class PropertyMatch:
    property_id: int
    listing_id: int
    score: float
    reasons: tuple[str, ...]
    shared_photos: int

    @property
    def signals(self) -> str:
        return ",".join(self.reasons)


def same_photo(a: tuple[str, str | None], b: tuple[str, str | None]) -> bool:
    phash_distance = hamming(a[0], b[0])
    if phash_distance <= PHASH_SAME:
        return True
    return phash_distance <= PHASH_NEAR and a[1] is not None and b[1] is not None and hamming(a[1], b[1]) <= DHASH_NEAR


def shingles(text: str) -> frozenset[str]:
    # Cifrele separate de litere: „52mp” și „52 mp” dau aceleași cuvinte.
    words = re.findall(r"[a-z]+|\d+", normalize(text))
    grams = frozenset(" ".join(words[i : i + SHINGLE_SIZE]) for i in range(len(words) - SHINGLE_SIZE + 1))
    return grams if len(grams) >= MIN_SHINGLES else frozenset()


def distance_m(a: Candidate, b: Candidate) -> float | None:
    if None in (a.latitude, a.longitude, b.latitude, b.longitude):
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (a.latitude, a.longitude, b.latitude, b.longitude))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def contradiction(a: Candidate, b: Candidate) -> str | None:
    """Un atribut cunoscut la ambele care arată clar că sunt apartamente diferite."""
    if a.area_m2 is not None and b.area_m2 is not None and abs(a.area_m2 - b.area_m2) > AREA_CONTRADICTION_M2:
        return "area"
    if a.floor is not None and b.floor is not None and a.floor != b.floor:
        return "floor"
    if a.rooms is not None and b.rooms is not None and a.rooms != b.rooms:
        return "rooms"
    distance = distance_m(a, b)
    if distance is not None and distance > FAR_METERS:
        return "distance"
    return None


def score_pair(
    own: Candidate, other: Candidate, common: set[str], busy_phones: frozenset[str] = frozenset()
) -> tuple[float, list[str], int] | None:
    """Scorul combinat pentru două anunțuri și semnalele care l-au produs; None la o contradicție.

    `common` sunt shingles-urile de șablon, `busy_phones` telefoanele care apar deja la mai multe apartamente.
    """
    if contradiction(own, other):
        return None
    score, reasons = 0.0, []
    shared = sum(1 for mine in own.photos if any(same_photo(mine, theirs) for theirs in other.photos))
    if shared >= 2:
        score += WEIGHTS["photos_many"]
    elif shared == 1:
        score += WEIGHTS["photos_one"]
    if shared:
        reasons.append(f"photos:{shared}")
    own_text, other_text = own.shingles - common, other.shingles - common
    if own_text and other_text:
        similarity = len(own_text & other_text) / len(own_text | other_text)
        if similarity >= TEXT_WEAK:
            score += WEIGHTS["text_strong" if similarity >= TEXT_STRONG else "text_weak"]
            reasons.append(f"text:{similarity:.2f}")
    if (
        own.phone_hash
        and own.phone_hash == other.phone_hash
        and own.phone_hash not in busy_phones
        and not (own.is_agency or other.is_agency)
    ):
        score += WEIGHTS["phone"]
        reasons.append("phone")
    if own.area_m2 is not None and other.area_m2 is not None and abs(own.area_m2 - other.area_m2) <= AREA_TOLERANCE_M2:
        score += WEIGHTS["area"]
        reasons.append("area")
    if own.floor is not None and own.floor == other.floor:
        score += WEIGHTS["floor"]
        reasons.append("floor")
    if own.rooms is not None and own.rooms == other.rooms:
        score += WEIGHTS["rooms"]
        reasons.append("rooms")
    distance = distance_m(own, other)
    if distance is not None and distance <= NEAR_METERS:
        score += WEIGHTS["near"]
        reasons.append("near")
    if not shared and not IDENTITY_SIGNALS & set(reasons):
        score = min(score, MATCH_THRESHOLD - 0.05)
    return round(min(score, 1.0), 4), reasons, shared


def _candidates(session: Session, listing: Listing) -> list[Candidate]:
    query = select(Listing).where(Listing.id != listing.id, Listing.property_id.is_not(None))
    if listing.area_m2 is not None:  # suprafețe incompatibile nu pot trece de verificarea de contradicții
        query = query.where(
            or_(
                Listing.area_m2.is_(None),
                Listing.area_m2.between(
                    listing.area_m2 - AREA_CONTRADICTION_M2, listing.area_m2 + AREA_CONTRADICTION_M2
                ),
            )
        )
    others = session.scalars(query).all()
    photos: dict[int, list[tuple[str, str | None]]] = {}
    for listing_id, phash, dhash in session.execute(
        select(Photo.listing_id, Photo.phash, Photo.dhash).where(
            Photo.listing_id.in_([o.id for o in others]), Photo.phash.is_not(None)
        )
    ):
        if is_informative(phash):
            photos.setdefault(listing_id, []).append((phash, dhash))
    return [_candidate(other, photos.get(other.id, [])) for other in others]


def _candidate(listing: Listing, photos: list[tuple[str, str | None]]) -> Candidate:
    return Candidate(
        listing_id=listing.id,
        property_id=listing.property_id,
        area_m2=listing.area_m2,
        floor=listing.floor,
        rooms=listing.rooms,
        latitude=listing.latitude,
        longitude=listing.longitude,
        phone_hash=listing.phone_hash,
        is_agency=listing.publisher_type == "agency",
        photos=tuple(photos),
        shingles=shingles(f"{listing.title} {listing.description}"),
    )


def find_matching_property(session: Session, listing: Listing) -> PropertyMatch | None:
    """Cel mai bun apartament existent pentru anunț, dacă scorul combinat trece de prag."""
    own_photos = [(p.phash, p.dhash) for p in listing.photos if p.phash and is_informative(p.phash)]
    own = _candidate(listing, own_photos)
    others = _candidates(session, listing)
    counts = Counter(gram for candidate in [own, *others] for gram in candidate.shingles)
    common = {gram for gram, count in counts.items() if count > COMMON_SHINGLE_LISTINGS}
    phone_properties: dict[str, set[int]] = {}
    for other in others:
        if other.phone_hash:
            phone_properties.setdefault(other.phone_hash, set()).add(other.property_id)
    busy_phones = frozenset(phone for phone, props in phone_properties.items() if len(props) > PHONE_MAX_PROPERTIES)
    best: PropertyMatch | None = None
    for other in others:
        scored = score_pair(own, other, common, busy_phones)
        if scored is None or scored[0] < MATCH_THRESHOLD:
            continue
        score, reasons, shared = scored
        if best is None or score > best.score:
            best = PropertyMatch(other.property_id, other.listing_id, score, tuple(reasons), shared)
    return best


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
        listing.match_reason, listing.match_signals = "score", found.signals
        listing.shared_photos, listing.match_score, listing.matched_listing_id = (
            found.shared_photos,
            found.score,
            found.listing_id,
        )
    else:
        prop = Property()
        session.add(prop)
        session.flush()
        listing.match_reason, listing.match_signals, listing.shared_photos = "new", None, 0
        listing.match_score, listing.matched_listing_id = None, None
    listing.property = prop
    session.flush()
    if found is not None:
        previous = find_republished_from(session, listing)
        listing.republished_from_id = previous.id if previous else None
    return prop  # type: ignore[return-value]
