"""Storia.ro: datele vin din JSON-ul Next.js (`__NEXT_DATA__`) al paginilor."""

from __future__ import annotations

from collections.abc import Iterator

from bs4 import BeautifulSoup

from ..parsing import (
    detect_balcony,
    find_floor_in_text,
    parse_area,
    parse_floor_value,
    parse_price,
    to_eur,
)
from ..schema import ListingData
from .base import Adapter, dig, find_objects_with_key, next_data, parse_datetime

BASE_URL = "https://www.storia.ro"

_ROOM_WORDS = {
    "ONE": 1,
    "TWO": 2,
    "THREE": 3,
    "FOUR": 4,
    "FIVE": 5,
    "SIX": 6,
    "SEVEN": 7,
    "EIGHT": 8,
    "NINE": 9,
    "TEN": 10,
    "MORE": 11,
}


def _rooms(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value.isdigit():
            return int(value)
        return _ROOM_WORDS.get(value.upper())
    return None


def _offer_url(item: dict) -> str:
    slug = item.get("slug")
    if slug:
        return f"{BASE_URL}/ro/oferta/{slug}"
    href = str(item.get("href") or item.get("url") or "")
    href = href.replace("[lang]/ad/", "ro/oferta/")
    return href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"


def _image_urls(images: object) -> list[str]:
    urls = []
    for image in images or []:
        if isinstance(image, dict):
            url = image.get("large") or image.get("medium") or image.get("small") or image.get("thumbnail")
            if url:
                urls.append(url)
        elif isinstance(image, str):
            urls.append(image)
    return urls


def _location_text(location: object) -> str:
    parts = []
    for loc in dig(location, "reverseGeocoding", "locations", default=[]) or []:
        if isinstance(loc, dict) and loc.get("fullName"):
            parts.append(loc["fullName"])
    address = dig(location, "address", default={}) or {}
    for key in ("street", "district", "subdistrict", "city"):
        value = address.get(key) if isinstance(address, dict) else None
        name = value.get("name") if isinstance(value, dict) else value
        if isinstance(name, str):
            parts.append(name)
    return ", ".join(dict.fromkeys(parts))


def _html_to_text(html: str | None) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


class StoriaAdapter(Adapter):
    name = "storia"
    page_param = "page"

    def parse_search(self, html: str, url: str) -> Iterator[ListingData]:
        data = next_data(html)
        if data is None:
            return
        items = dig(data, "props", "pageProps", "data", "searchAds", "items")
        if not isinstance(items, list):
            # Structura s-a schimbat: căutăm orice obiect care arată ca un anunț.
            items = [obj for obj in find_objects_with_key(data, "totalPrice") if obj.get("id")]
        for item in items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            price = dig(item, "totalPrice", "value")
            currency = dig(item, "totalPrice", "currency")
            title = item.get("title") or ""
            floor = parse_floor_value(item.get("floorNumber")) or find_floor_in_text(title)
            agency = dig(item, "agency", "name")
            yield ListingData(
                platform=self.name,
                external_id=str(item["id"]),
                url=_offer_url(item),
                title=title,
                location_text=_location_text(item.get("location")),
                price=float(price) if isinstance(price, (int, float)) else None,
                currency=currency,
                price_eur=to_eur(float(price), currency, self.settings.eur_ron)
                if isinstance(price, (int, float))
                else None,
                area_m2=parse_area(item.get("areaInSquareMeters")),
                rooms=_rooms(item.get("roomsNumber")),
                floor=floor,
                balcony=detect_balcony(title),
                publisher_name=agency,
                publisher_type="owner" if item.get("isPrivateOwner") else ("agency" if agency else "unknown"),
                posted_at=parse_datetime(item.get("dateCreated") or item.get("createdAt")),
                photo_urls=_image_urls(item.get("images")),
            )

    def parse_detail(self, html: str, url: str) -> ListingData:
        data = next_data(html)
        ad = dig(data, "props", "pageProps", "ad", default={}) or {}
        characteristics = {c.get("key"): c for c in ad.get("characteristics", []) or [] if isinstance(c, dict)}

        def char(key: str) -> object:
            entry = characteristics.get(key) or {}
            return entry.get("value") or entry.get("localizedValue")

        price_entry = characteristics.get("price") or {}
        price = price_entry.get("value")
        currency = price_entry.get("currency")
        price_value, _ = parse_price(price)
        description = _html_to_text(ad.get("description"))
        title = ad.get("title") or ""
        features = " ".join(
            str(f) for f in (ad.get("features") or []) + list(_flatten_features(ad.get("featuresByCategory")))
        )
        balcony = detect_balcony(features)
        if balcony is None:
            balcony = detect_balcony(description)
        floor = parse_floor_value(char("floor_no")) or find_floor_in_text(f"{title} {description}")
        advertiser = str(ad.get("advertiserType") or "").lower()
        agency_name = dig(ad, "agency", "name")
        return ListingData(
            platform=self.name,
            external_id=str(ad.get("id") or ""),
            url=url,
            title=title,
            description=description,
            location_text=_location_text(ad.get("location")),
            price=price_value,
            currency=currency,
            price_eur=to_eur(price_value, currency, self.settings.eur_ron),
            area_m2=parse_area(char("m")),
            rooms=_rooms(char("rooms_num")),
            floor=floor,
            balcony=balcony,
            latitude=dig(ad, "location", "coordinates", "latitude"),
            longitude=dig(ad, "location", "coordinates", "longitude"),
            publisher_name=agency_name or dig(ad, "owner", "name"),
            publisher_type="owner" if advertiser == "private" else ("agency" if advertiser or agency_name else None),
            posted_at=parse_datetime(ad.get("createdAt") or ad.get("dateCreated")),
            photo_urls=_image_urls(ad.get("images")),
            is_detail=True,
            extra={"build_year": char("build_year")} if char("build_year") else {},
        )


def _flatten_features(groups: object) -> Iterator[str]:
    for group in groups or []:
        if isinstance(group, dict):
            for value in group.get("values") or []:
                yield str(value)
