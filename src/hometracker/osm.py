"""Clădirile din OpenStreetMap (Overpass): anul construcției (`start_date`) lângă coordonatele unui apartament.

Vezi ADR 0005: worker-ul face cel mult câteva interogări pe rulare și le păstrează în `osm_building`;
interfața și rezumatul citesc doar cache-ul.
"""

from __future__ import annotations

import json
import logging
import math
import re
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from .buildyear import MAX_YEAR, MIN_YEAR, Evidence
from .models import Listing, OsmBuilding, utcnow

log = logging.getLogger(__name__)

SEARCH_RADIUS_M = 30
ATTRIBUTION = "© OpenStreetMap contributors"
# Serviciile OpenStreetMap cer un User-Agent care identifică aplicația (unul de browser primește 406).
OSM_HEADERS = {
    "User-Agent": "home-tracker/0.1 (personal tool; https://github.com/dmdafsm/home-tracker)",
    "Accept": "*/*",
}
# Instanța publică e des aglomerată; 502/503/429 sunt reîncercate de PoliteClient, apoi rularea renunță.
OSM_TIMEOUT_S = 60


def coordinate_key(latitude: float, longitude: float) -> tuple[float, float]:
    """Coordonatele rotunjite (~10 m), ca două anunțuri din același bloc să folosească aceeași interogare."""
    return round(latitude, 4), round(longitude, 4)


def overpass_query(latitude: float, longitude: float) -> str:
    return (
        f"[out:json][timeout:20];"
        f'(way(around:{SEARCH_RADIUS_M},{latitude},{longitude})["building"];'
        f'relation(around:{SEARCH_RADIUS_M},{latitude},{longitude})["building"];);'
        f"out tags center;"
    )


def parse_start_date(value: str | None) -> tuple[int, int] | None:
    """`1978`, `1978-05-01`, `~1975`, `1970s`, `C20`, `1975..1980` -> interval de ani."""
    if not value:
        return None
    text = value.strip().lower().lstrip("~")
    if match := re.fullmatch(r"((?:19|20)\d{2})\s*\.\.\s*((?:19|20)\d{2})", text):
        low, high = int(match.group(1)), int(match.group(2))
    elif match := re.fullmatch(r"((?:19|20)\d)0s", text):
        low = int(match.group(1)) * 10
        high = low + 9
    elif match := re.fullmatch(r"c(\d{2})", text):
        century = int(match.group(1))
        low, high = (century - 1) * 100, (century - 1) * 100 + 99
    elif match := re.match(r"((?:19|20)\d{2})", text):
        low = high = int(match.group(1))
    else:
        return None
    if not (MIN_YEAR <= low <= MAX_YEAR) and not (MIN_YEAR <= high <= MAX_YEAR):
        return None
    return max(low, MIN_YEAR), min(high, MAX_YEAR)


def _distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def nearest_dated_building(response: dict, latitude: float, longitude: float) -> dict | None:
    """Cea mai apropiată clădire cu `start_date` din răspunsul Overpass (sau cea mai apropiată, fără an)."""
    buildings = []
    for element in response.get("elements") or []:
        center = element.get("center") or {}
        tags = element.get("tags") or {}
        if "lat" not in center or "lon" not in center:
            continue
        distance = _distance(latitude, longitude, center["lat"], center["lon"])
        buildings.append((parse_start_date(tags.get("start_date")) is None, distance, element))
    if not buildings:
        return None
    return min(buildings, key=lambda item: (item[0], item[1]))[2]


def lookup(session: Session, client, overpass_url: str, latitude: float, longitude: float) -> OsmBuilding:
    """Interoghează Overpass pentru o locație și salvează rezultatul (și „nimic găsit”)."""
    lat_key, lon_key = coordinate_key(latitude, longitude)
    url = f"{overpass_url}?{urlencode({'data': overpass_query(lat_key, lon_key)})}"
    response = client.get(url, archive=False, headers=OSM_HEADERS, timeout=OSM_TIMEOUT_S)
    element = nearest_dated_building(json.loads(response.text), lat_key, lon_key)
    tags = (element or {}).get("tags") or {}
    row = OsmBuilding(
        lat_key=lat_key,
        lon_key=lon_key,
        osm_id=f"{element['type']}/{element['id']}" if element else None,
        start_date=tags.get("start_date"),
        levels=tags.get("building:levels"),
        fetched_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def cached(session: Session, latitude: float, longitude: float) -> OsmBuilding | None:
    lat_key, lon_key = coordinate_key(latitude, longitude)
    return session.scalar(select(OsmBuilding).where(OsmBuilding.lat_key == lat_key, OsmBuilding.lon_key == lon_key))


def evidence(session: Session, listings: list[Listing]) -> Evidence | None:
    """Anul din OpenStreetMap pentru oricare dintre anunțurile cu coordonate ale apartamentului."""
    for listing in listings:
        if listing.latitude is None or listing.longitude is None:
            continue
        row = cached(session, listing.latitude, listing.longitude)
        years = parse_start_date(row.start_date) if row else None
        if years:
            detail = f"OpenStreetMap {row.osm_id}: start_date={row.start_date}"
            if row.levels:
                detail += f", {row.levels} niveluri"
            return Evidence(years[0], years[1], "osm", detail)
    return None


def pending_locations(session: Session, limit: int) -> list[tuple[float, float]]:
    """Coordonatele apartamentelor grupate care nu au încă o interogare în cache."""
    if limit <= 0:
        return []
    done = {(row.lat_key, row.lon_key) for row in session.scalars(select(OsmBuilding))}
    found: list[tuple[float, float]] = []
    rows = session.execute(
        select(Listing.latitude, Listing.longitude)
        .where(Listing.property_id.is_not(None), Listing.latitude.is_not(None), Listing.longitude.is_not(None))
        .order_by(Listing.first_seen.desc())
    )
    for latitude, longitude in rows:
        key = coordinate_key(latitude, longitude)
        if key not in done and key not in found:
            found.append(key)
            if len(found) >= limit:
                break
    return found
