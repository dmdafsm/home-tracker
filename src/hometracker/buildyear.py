"""Estimarea anului construcției: un interval, cu sursa lui, din anunțuri, tipologie și OpenStreetMap.

Nu există o sursă publică unică și gratuită cu anul fiecărui bloc. Combinăm ce avem și arătăm de
unde vine fiecare parte. Ordinea de încredere: OpenStreetMap (`start_date` pe clădire), apoi anii
declarați în anunțuri (confirmați de cel puțin două anunțuri diferite), apoi tipologia blocului.
Pentru apartamentul ales, anul se confirmă manual din documente (vezi docs/surse-an-constructie.md).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Listing
from .parsing import normalize

# Anii plauzibili pentru un bloc de locuințe din Cluj.
MIN_YEAR, MAX_YEAR = 1900, 2030
# Anunțuri din același bloc: coordonate la cel mult atâția metri.
BLOCK_RADIUS_M = 40
# Doi ani declarați „confirmă” aceeași valoare dacă diferă cu cel mult atât.
AGREEMENT_YEARS = 2

_YEAR_IN_TEXT = [
    re.compile(
        r"\b(?:an(?:ul)?\s+(?:de\s+)?constructi[ea]|constructi[ea]|construit|finalizat|receptionat)\s*:?\s*"
        r"(?:in|din|anul)?\s*:?\s*((?:19|20)\d{2})\b"
    ),
    re.compile(r"\bbloc(?:ul)?\s+(?:construit\s+)?(?:in|din)\s+((?:19|20)\d{2})\b"),
]
_DECADE_IN_TEXT = re.compile(r"\b(?:bloc|constructie|construit)\w*\s+(?:din|in)\s+anii\s*'?\s*(\d0|19\d0)\b")
_TOTAL_FLOORS = [
    re.compile(r"\bp\s*\+\s*(\d{1,2})\b"),
    re.compile(r"\betaj(?:ul)?\s*:?\s*(?:\d{1,2}|parter)\s*(?:din|/)\s*(\d{1,2})\b"),
]
_NEW_BUILD = re.compile(r"\b(bloc nou|constructie noua|ansamblu rezidential|dezvoltator|predare la cheie|nzeb)\b")
_PANEL = re.compile(r"\b(panou|panouri|prefabricat)\b")
_BRICK = re.compile(r"\b(caramida)\b")


@dataclass(frozen=True)
class Evidence:
    low: int
    high: int
    source: str  # osm / declared / text / typology
    detail: str


@dataclass
class YearEstimate:
    low: int
    high: int
    confidence: str  # mare / medie / mică
    evidence: list[Evidence] = field(default_factory=list)
    conflicts: list[Evidence] = field(default_factory=list)

    @property
    def interval(self) -> str:
        return str(self.low) if self.low == self.high else f"{self.low}–{self.high}"

    @property
    def summary(self) -> str:
        parts = []
        counts: dict[str, int] = {}
        for item in self.evidence:
            counts[item.source] = counts.get(item.source, 0) + 1
        if counts.get("osm"):
            parts.append("OpenStreetMap")
        listings = counts.get("declared", 0) + counts.get("text", 0)
        if listings:
            parts.append(f"{listings} {'anunț' if listings == 1 else 'anunțuri'}")
        if counts.get("typology"):
            parts.append("tipologie")
        return f"{self.interval}, din {' și '.join(parts)}"


def _year(value: object) -> int | None:
    match = re.search(r"\b((?:19|20)\d{2})\b", str(value or ""))
    year = int(match.group(1)) if match else None
    return year if year and MIN_YEAR <= year <= MAX_YEAR else None


def listing_evidence(listing: Listing) -> list[Evidence]:
    """Ce spune un anunț despre anul blocului: câmpul platformei și textul."""
    found = []
    declared = _year((listing.extra or {}).get("build_year"))
    if declared:
        found.append(
            Evidence(declared, declared, "declared", f"{listing.platform} #{listing.id}: câmpul „an construcție”")
        )
    text = normalize(f"{listing.title} {listing.description}")
    for pattern in _YEAR_IN_TEXT:
        match = pattern.search(text)
        if match and (year := _year(match.group(1))) and year != declared:
            found.append(Evidence(year, year, "text", f"{listing.platform} #{listing.id}: „{match.group(0)}”"))
            break
    decade = _DECADE_IN_TEXT.search(text)
    if decade and not found:
        start = int(decade.group(1))
        start = start + 1900 if start < 100 else start
        found.append(Evidence(start, start + 9, "text", f"{listing.platform} #{listing.id}: „{decade.group(0)}”"))
    return found


def typology_evidence(listings: list[Listing]) -> list[Evidence]:
    """Tipologia blocului, din textul tuturor anunțurilor apartamentului. Încredere mică."""
    text = normalize(" ".join(f"{item.title} {item.description}" for item in listings))
    floors = None
    for pattern in _TOTAL_FLOORS:
        if match := pattern.search(text):
            floors = int(match.group(1))
            break
    if _NEW_BUILD.search(text):
        return [Evidence(2005, MAX_YEAR, "typology", "bloc nou / ansamblu rezidențial")]
    if _PANEL.search(text):
        return [Evidence(1962, 1990, "typology", "bloc din panouri prefabricate")]
    if floors in (4, 5) and _BRICK.search(text):
        return [Evidence(1955, 1990, "typology", f"bloc de cărămidă P+{floors}")]
    if floors and floors >= 8:
        return [Evidence(1968, 1990, "typology", f"bloc înalt P+{floors}, tipic anilor '70–'80")]
    return []


def _distance_m(a: Listing, b: Listing) -> float | None:
    if None in (a.latitude, a.longitude, b.latitude, b.longitude):
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (a.latitude, a.longitude, b.latitude, b.longitude))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def same_block(listings: list[Listing], others: list[Listing]) -> list[Listing]:
    """Anunțurile altor apartamente din același bloc (coordonate apropiate)."""
    found = []
    for other in others:
        distances = [d for listing in listings if (d := _distance_m(listing, other)) is not None]
        if distances and min(distances) <= BLOCK_RADIUS_M:
            found.append(other)
    return found


def estimate_for(session: Session, listings: list[Listing]) -> YearEstimate | None:
    """Estimarea pentru un apartament: anunțurile lui, cele din același bloc și cache-ul OpenStreetMap."""
    from . import osm  # osm.py folosește Evidence de aici; importul local evită dependența circulară

    property_ids = {listing.property_id for listing in listings}
    located = [listing for listing in listings if listing.latitude is not None and listing.longitude is not None]
    neighbours: list[Listing] = []
    if located:
        margin = 0.001  # ~100 m: filtrul grosier în SQL, distanța exactă în same_block
        lats = [listing.latitude for listing in located]
        lons = [listing.longitude for listing in located]
        candidates = session.scalars(
            select(Listing).where(
                Listing.latitude.between(min(lats) - margin, max(lats) + margin),
                Listing.longitude.between(min(lons) - margin, max(lons) + margin),
                Listing.property_id.not_in(property_ids),
            )
        ).all()
        neighbours = same_block(located, list(candidates))
    return estimate(listings, neighbours, osm.evidence(session, listings))


def estimate(
    listings: list[Listing], neighbours: list[Listing] = (), osm: Evidence | None = None
) -> YearEstimate | None:
    """Intervalul cel mai sigur pe care îl susțin dovezile, sau None dacă nu avem nimic."""
    from_listings = [item for listing in [*listings, *neighbours] for item in listing_evidence(listing)]
    exact = sorted(
        {(item.low, item.detail): item for item in from_listings if item.low == item.high}.values(),
        key=lambda item: item.low,
    )
    typology = typology_evidence(listings)

    if osm is not None:
        agreeing = [
            item
            for item in from_listings
            if item.low - AGREEMENT_YEARS <= osm.high and item.high + AGREEMENT_YEARS >= osm.low
        ]
        conflicts = [item for item in from_listings if item not in agreeing]
        return YearEstimate(osm.low, osm.high, "mare", [osm, *agreeing], conflicts)

    if exact:
        # Cel mai mare grup de ani care se confirmă reciproc.
        groups = [[item for item in exact if abs(item.low - anchor.low) <= AGREEMENT_YEARS] for anchor in exact]
        best = max(groups, key=len)
        low, high = min(item.low for item in best), max(item.high for item in best)
        sources = {item.detail.split(":")[0] for item in best}
        confidence = "medie" if len(sources) >= 2 else "mică"
        conflicts = [item for item in from_listings if item not in best]
        support = [item for item in typology if item.low <= low and item.high >= high]
        return YearEstimate(low, high, confidence, [*best, *support], conflicts)

    ranges = [item for item in from_listings if item.low != item.high]
    if ranges:
        item = ranges[0]
        return YearEstimate(item.low, item.high, "mică", [item, *typology])
    if typology:
        item = typology[0]
        return YearEstimate(item.low, item.high, "mică", [item])
    return None
