"""Imobiliare.ro: adapter generic (JSON-LD + linkuri + text), de ajustat pe pagini reale.

Structura paginilor Imobiliare.ro nu e documentată și se schimbă des. Adapterul citește
ce e cel mai stabil: datele schema.org (JSON-LD) și linkurile către oferte, apoi
completează din textul paginii de detaliu. Pagina de căutare dă doar URL-ul și titlul,
restul vine din pagina de detaliu.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..parsing import (
    detect_balcony,
    find_phone_in_text,
    normalize,
    parse_area,
    parse_floor_value,
    parse_price,
    to_eur,
)
from ..schema import ListingData
from .base import Adapter, dig, json_ld

BASE_URL = "https://www.imobiliare.ro"
_OFFER_PATH = re.compile(r"^/(oferta|vanzare-apartamente)/.+")
_FLOOR_IN_PAGE = re.compile(r"\betaj\s*:?\s*(parter inalt|parter|demisol|mansarda|\d{1,2}(?:\s*/\s*\d{1,2})?)\b")
_AREA_IN_PAGE = re.compile(r"suprafata utila\s*:?\s*(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:mp|m2)")
_ROOMS_IN_PAGE = re.compile(r"\b(\d{1,2})\s*camere\b|\bgarsoniera\b")


def _offer_id(url: str) -> str | None:
    path = urlparse(url).path.rstrip("/")
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2:
        return None
    if segments[0] == "vanzare-apartamente" and len(segments) < 4:
        return None  # pagină de listă (oraș / cartier), nu ofertă
    last = segments[-1]
    match = re.search(r"([A-Za-z0-9]{6,})$", last)
    return match.group(1) if match else last


class ImobiliareAdapter(Adapter):
    name = "imobiliare"
    page_param = "page"

    def parse_search(self, html: str, url: str) -> Iterator[ListingData]:
        found: dict[str, ListingData] = {}
        for block in json_ld(html):
            if block.get("@type") != "ItemList":
                continue
            for element in block.get("itemListElement") or []:
                item_url = dig(element, "url") or dig(element, "item", "url")
                name = dig(element, "name") or dig(element, "item", "name") or ""
                if isinstance(item_url, str):
                    self._add(found, urljoin(BASE_URL, item_url), str(name))
        if not found:
            soup = BeautifulSoup(html, "lxml")
            for anchor in soup.find_all("a", href=True):
                href = urljoin(BASE_URL, anchor["href"])
                if urlparse(href).netloc.endswith("imobiliare.ro") and _OFFER_PATH.match(urlparse(href).path):
                    self._add(found, href, anchor.get_text(" ", strip=True))
        yield from found.values()

    def _add(self, found: dict[str, ListingData], url: str, title: str) -> None:
        offer_id = _offer_id(url)
        if not offer_id or offer_id in found:
            return
        found[offer_id] = ListingData(
            platform=self.name,
            external_id=offer_id,
            url=url.split("?")[0],
            title=title[:300],
        )

    def parse_detail(self, html: str, url: str) -> ListingData:
        soup = BeautifulSoup(html, "lxml")
        blocks = json_ld(html)
        offer_block = next(
            (b for b in blocks if b.get("@type") in ("Product", "Offer", "Apartment", "Residence",
                                                       "SingleFamilyResidence", "RealEstateListing")),
            {},
        )
        title = str(offer_block.get("name") or (soup.h1.get_text(" ", strip=True) if soup.h1 else ""))
        description = str(offer_block.get("description") or "")
        price, currency = parse_price(dig(offer_block, "offers", "price"))
        currency = dig(offer_block, "offers", "priceCurrency") or currency
        page_text = soup.get_text(" ", strip=True)
        norm = normalize(page_text)
        if price is None:
            price_match = re.search(r"(\d{2,3}[ .]\d{3})\s*(?:€|eur)", norm)
            if price_match:
                price, currency = parse_price(price_match.group(0))
        floor_match = _FLOOR_IN_PAGE.search(norm)
        area_match = _AREA_IN_PAGE.search(norm)
        rooms_match = _ROOMS_IN_PAGE.search(normalize(title)) or _ROOMS_IN_PAGE.search(norm)
        rooms = None
        if rooms_match:
            rooms = 1 if rooms_match.group(0) == "garsoniera" else int(rooms_match.group(1))
        images = offer_block.get("image") or []
        if isinstance(images, str):
            images = [images]
        return ListingData(
            platform=self.name,
            external_id=_offer_id(url) or "",
            url=url,
            title=title,
            description=description,
            location_text=str(dig(offer_block, "address", "addressLocality", default="") or ""),
            price=price,
            currency=currency,
            price_eur=to_eur(price, currency, self.settings.eur_ron),
            area_m2=parse_area(f"{area_match.group(1)} mp") if area_match else None,
            rooms=rooms,
            floor=parse_floor_value(floor_match.group(1)) if floor_match else None,
            balcony=detect_balcony(f"{title} {description}") if description else detect_balcony(page_text),
            latitude=dig(offer_block, "geo", "latitude"),
            longitude=dig(offer_block, "geo", "longitude"),
            phone=find_phone_in_text(description),
            photo_urls=[str(u if isinstance(u, str) else dig(u, "url") or "") for u in images if u],
            is_detail=True,
        )
