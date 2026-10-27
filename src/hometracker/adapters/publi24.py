"""Publi24: datele schema.org (JSON-LD) din căutare și din pagina de detaliu.

Verificat pe pagini reale în octombrie 2026: căutarea are un `ItemList` cu câte un `Product` pe anunț
(nume, descriere, URL relativ, poze, preț, monedă, localitate, suprafață), iar pagina de detaliu un
`Product` cu toate pozele și un tabel `attribute-label` / `attribute-value` (suprafață, camere, etaj).
Textele din JSON-LD conțin rânduri noi netransformate și entități HTML (`&#39;`). Telefonul apare doar
după un clic („Afișează”), deci nu îl citim; numele publicatorului e în `user-profile-info`.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..parsing import (
    detect_balcony,
    find_floor_in_text,
    find_phone_in_text,
    normalize,
    parse_area,
    parse_floor_value,
    parse_price,
    to_eur,
)
from ..schema import ListingData
from .base import Adapter, dig, json_ld

BASE_URL = "https://www.publi24.ro/"
_ROOMS_IN_URL = re.compile(r"/apartamente-(\d{1,2})-camer[ea]/")
_ROOMS_IN_TEXT = re.compile(r"\b(\d{1,2})\s*camer[ea]\b")
_AREA_IN_TEXT = re.compile(r"suprafata (?:utila|construita)?\s*:?\s*(\d{2,4}(?:[.,]\d{1,2})?)\s*(?:mp|m2)")


def _offer_id(url: str) -> str | None:
    """Id-ul anunțului e ultimul segment din URL: `.../anunt/<slug>/<id>.html`."""
    match = re.search(r"/anunt/[^/]+/([0-9a-z]{16,40})\.html$", urlparse(url).path)
    return match.group(1) if match else None


def _text(value: object) -> str:
    return html.unescape(str(value or "")).strip()


def _images(images: object) -> list[str]:
    urls = []
    for image in images if isinstance(images, list) else [images]:
        url = image.get("contentUrl") if isinstance(image, dict) else image
        if isinstance(url, str) and url.startswith("https://"):
            urls.append(url)
    return list(dict.fromkeys(urls))


def _rooms(url: str, text: str) -> int | None:
    match = _ROOMS_IN_URL.search(url) or _ROOMS_IN_TEXT.search(normalize(text))
    if match:
        return int(match.group(1))
    return 1 if "garsoniera" in normalize(text) or "/apartamente-1-camera/" in url else None


class Publi24Adapter(Adapter):
    name = "publi24"
    page_param = "pag"

    def _from_product(self, product: dict, url: str, *, is_detail: bool) -> ListingData | None:
        offer_id = _offer_id(url)
        if not offer_id:
            return None
        title = _text(product.get("name"))
        description = _text(product.get("description"))
        price, currency = parse_price(dig(product, "offers", "price"))
        currency = dig(product, "offers", "priceCurrency") or currency
        address = dig(product, "offers", "availableAtOrFrom", "address", default={}) or {}
        location = ", ".join(
            _text(address.get(key)) for key in ("addressLocality", "addressRegion") if address.get(key)
        )
        # În căutare suprafața vine adesea „0”: necunoscută, nu zero. Atunci o căutăm în descriere.
        area = parse_area(dig(product, "offers", "itemOffered", "floorSize", "value")) or None
        if area is None:
            match = _AREA_IN_TEXT.search(normalize(description))
            area = float(match.group(1).replace(",", ".")) if match else None
        text = f"{title} {description}"
        return ListingData(
            platform=self.name,
            external_id=offer_id,
            url=url,
            title=title,
            description=description,
            location_text=location,
            price=price,
            currency=currency,
            price_eur=to_eur(price, currency, self.settings.eur_ron),
            area_m2=area,
            rooms=_rooms(url, text),
            floor=find_floor_in_text(text),
            balcony=detect_balcony(description),
            phone=find_phone_in_text(description),
            photo_urls=_images(product.get("image")),
            is_detail=is_detail,
        )

    def parse_search(self, page: str, url: str) -> Iterator[ListingData]:
        for block in json_ld(page):
            if block.get("@type") != "ItemList":
                continue
            for element in block.get("itemListElement") or []:
                product = element.get("item", element) if isinstance(element, dict) else None
                if not isinstance(product, dict) or not product.get("url"):
                    continue
                item = self._from_product(product, urljoin(BASE_URL, product["url"]), is_detail=False)
                if item:
                    yield item

    def parse_detail(self, page: str, url: str) -> ListingData:
        product = next((block for block in json_ld(page) if block.get("@type") == "Product"), {})
        item = self._from_product(product, url, is_detail=True) or ListingData(
            platform=self.name, external_id=_offer_id(url) or "", url=url, is_detail=True
        )
        soup = BeautifulSoup(page, "lxml")
        attributes = {}
        for row in soup.select(".attribute-item"):
            label, value = row.select_one(".attribute-label"), row.select_one(".attribute-value")
            if label and value:
                attributes[normalize(label.get_text(" ", strip=True))] = " ".join(
                    value.get_text(" ", strip=True).split()
                )
        item.area_m2 = parse_area(attributes.get("suprafata utila")) or item.area_m2
        rooms = _ROOMS_IN_TEXT.search(normalize(attributes.get("numar camere", "")))
        item.rooms = int(rooms.group(1)) if rooms else item.rooms
        floor = attributes.get("etaj")
        if floor:
            item.floor = (
                parse_floor_value(floor.removeprefix("etaj ").strip()) or find_floor_in_text(floor) or item.floor
            )
        if attributes.get("an constructie"):
            item.extra["build_year"] = attributes["an constructie"]
        profile = soup.select_one(".user-profile-info")
        if profile:
            # „Telefon validat Cosmin . Vezi toate anunțurile 07xx xxx xxx Afișează”: numele vine după insignă.
            words = " ".join(profile.get_text(" ", strip=True).split())
            name = re.sub(r"^(telefon validat|cont verificat)\s*", "", words, flags=re.I).split(" . ")[0].strip()
            item.publisher_name = name[:120] or None
        return item
