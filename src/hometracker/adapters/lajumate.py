"""Lajumate: JSON-ul Next.js din pagină (`__NEXT_DATA__`).

Verificat pe pagini reale în octombrie 2026: căutarea are anunțurile în `props.pageProps.adsServer`
(28 pe pagină, cele mai noi întâi, paginare cu `?page=N`), pagina de detaliu în `props.pageProps.adData`.
Caracteristicile sunt o listă de câmpuri (`ad_fields` în căutare, `fields` în detaliu) cu `name` și
`value`: rooms, real_estate_dimensions, real_estate_floor („Parter”, „Demisol”, „5”, „13 sau mai mare”),
real_estate_year. Publicatorul vine cu nume și telefon (dacă nu e ascuns) și, în detaliu, cu tipul
(„Companie”). Multe anunțuri sunt copii de pe homezz.ro (`isCopy`); deduplicarea le leagă de original.

Pozele nu se descarcă: serviciul de imagini răspunde „Image not found” cererilor care nu vin din paginile
site-ului (verificat cu URL-ul exact din `<img>`). Nu imităm contextul unui browser ca să trecem de asta;
anunțurile Lajumate se leagă de apartamente prin text, telefon și atribute. URL-urile pozelor rămân în
`extra["photo_urls"]`, pentru referință.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator

from ..parsing import (
    detect_balcony,
    find_floor_in_text,
    find_phone_in_text,
    parse_area,
    parse_floor_value,
    parse_price,
    to_eur,
)
from ..schema import ListingData
from .base import Adapter, dig, html_to_text, next_data, parse_datetime

BASE_URL = "https://lajumate.ro"
# Așa apar pozele în paginile site-ului (calea din JSON + numele fișierului).
IMAGE_BASE = "https://api-preprod.lajumate.ro/opt-image/"
_HIGH_FLOOR = re.compile(r"^(\d{1,2}) sau mai mare$")
# Câmpul de suprafață e completat de publicator; valori ca „2” sunt greșeli, nu apartamente de 2 mp.
MIN_AREA_M2 = 10


def _fields(ad: dict) -> dict[str, str]:
    fields = ad.get("ad_fields") or ad.get("fields") or []
    return {f.get("name"): str(f.get("value")) for f in fields if isinstance(f, dict) and f.get("value") is not None}


def _floor(value: str | None) -> str | None:
    if not value:
        return None
    high = _HIGH_FLOOR.match(value.strip().lower())
    return high.group(1) if high else parse_floor_value(value)


def _area(value: str | None) -> float | None:
    area = parse_area(value)
    return area if area is not None and area >= MIN_AREA_M2 else None


def _images(ad: dict) -> list[str]:
    urls = []
    for image in ad.get("images") or ([ad["mainImage"]] if ad.get("mainImage") else []):
        if isinstance(image, dict) and image.get("path") and image.get("name"):
            path, name = str(image["path"]).lstrip("/"), str(image["name"])
            # În căutare `path` se termină înaintea numelui fișierului, în detaliu îl conține deja.
            urls.append(IMAGE_BASE + (path if path.endswith(name) else path + name))
    return list(dict.fromkeys(urls))


def _description(value: object) -> str:
    text = html.unescape(str(value or ""))
    return html_to_text(text) if "<" in text else text.strip()


class LajumateAdapter(Adapter):
    name = "lajumate"
    page_param = "page"

    def _from_ad(self, ad: dict, *, is_detail: bool) -> ListingData | None:
        if not ad.get("id") or not ad.get("slug"):
            return None
        fields = _fields(ad)
        title = str(ad.get("title") or "")
        description = _description(ad.get("description"))
        price, _ = parse_price(ad.get("price"))
        currency = str(ad.get("currency") or "").upper() or None
        user = ad.get("user") or {}
        phone = None if user.get("hide_phone") else user.get("phone")
        user_type = str(user.get("type") or "").lower()
        publisher_type = (
            "agency" if user_type.startswith("compan") else ("owner" if user_type.startswith("persoan") else None)
        )
        city = dig(ad, "city", "name")
        county = dig(ad, "city", "county", "name")
        extra = {}
        if fields.get("real_estate_year"):
            extra["build_year"] = fields["real_estate_year"]
        if isinstance(ad.get("isCopy"), dict) and ad["isCopy"].get("site"):
            extra["copy_of"] = ad["isCopy"]["site"]
        rooms = fields.get("rooms")
        return ListingData(
            platform=self.name,
            external_id=str(ad["id"]),
            url=f"{BASE_URL}/ad/{ad['slug']}-{ad['id']}",
            title=title,
            description=description,
            location_text=", ".join(part for part in (city, county) if part),
            price=price,
            currency=currency,
            price_eur=to_eur(price, currency, self.settings.eur_ron),
            area_m2=_area(fields.get("real_estate_dimensions")),
            rooms=int(rooms) if rooms and rooms.isdigit() else None,
            floor=_floor(fields.get("real_estate_floor")) or find_floor_in_text(f"{title} {description}"),
            balcony=detect_balcony(description),
            publisher_name=user.get("name"),
            publisher_type=publisher_type,
            phone=phone or find_phone_in_text(description),
            posted_at=parse_datetime(ad.get("listed_at")),
            photo_urls=[],
            is_detail=is_detail,
            extra={**extra, "photo_urls": _images(ad)},
        )

    def parse_search(self, html_text: str, url: str) -> Iterator[ListingData]:
        ads = dig(next_data(html_text), "props", "pageProps", "adsServer", default=[]) or []
        for ad in ads:
            if isinstance(ad, dict) and (item := self._from_ad(ad, is_detail=False)):
                yield item

    def parse_detail(self, html_text: str, url: str) -> ListingData:
        ad = dig(next_data(html_text), "props", "pageProps", "adData", default={}) or {}
        item = self._from_ad(ad, is_detail=True) if isinstance(ad, dict) else None
        return item or ListingData(platform=self.name, external_id="", url=url, is_detail=True)
