"""OLX.ro: datele vin din `window.__PRERENDERED_STATE__`, cu JSON-LD ca rezervă."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

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
from .base import Adapter, dig, json_ld, parse_datetime

_STATE = re.compile(r"window\.__PRERENDERED_STATE__\s*=\s*(\"(?:[^\"\\]|\\.)*\")", re.S)


def prerendered_state(html: str) -> dict | None:
    match = _STATE.search(html)
    if not match:
        return None
    try:
        return json.loads(json.loads(match.group(1)))
    except (json.JSONDecodeError, TypeError):
        return None


def _photo_url(url: str) -> str:
    return url.replace("{width}", "1280").replace("{height}", "960")


def _params(ad: dict) -> dict[str, dict]:
    """Parametrii anunțului indexați după un nume normalizat: floor, area, rooms."""
    out: dict[str, dict] = {}
    for param in ad.get("params") or []:
        if not isinstance(param, dict):
            continue
        key = normalize(str(param.get("key") or ""))
        name = normalize(str(param.get("name") or ""))
        if key in ("floor", "etaj") or name.startswith("etaj"):
            out["floor"] = param
        elif key in ("m", "surface", "suprafata") or "suprafata" in name:
            out["area"] = param
        elif key in ("rooms", "camere") or "camer" in name:
            out["rooms"] = param
    return out


def _param_value(param: dict | None) -> object:
    if not param:
        return None
    for key in ("normalizedValue", "value", "label"):
        value = param.get(key)
        if isinstance(value, dict):
            value = value.get("label") or value.get("key") or value.get("value")
        if value not in (None, ""):
            return value
    return None


def _is_storia_mirror(ad: dict) -> bool:
    """OLX afișează și anunțuri Storia; le lăsăm adapterului Storia."""
    external = str(ad.get("externalUrl") or "")
    partner = normalize(str(dig(ad, "partner", "code", default="") or ""))
    return "storia.ro" in external or partner in ("storia", "otodom")


def _description_text(html: str | None) -> str:
    if not html:
        return ""
    return BeautifulSoup(str(html), "lxml").get_text(" ", strip=True)


class OlxAdapter(Adapter):
    name = "olx"
    page_param = "page"

    def _from_ad(self, ad: dict, *, is_detail: bool) -> ListingData | None:
        if not ad.get("id") or _is_storia_mirror(ad):
            return None
        params = _params(ad)
        price = dig(ad, "price", "regularPrice", "value")
        currency = dig(ad, "price", "regularPrice", "currencyCode")
        if price is None:
            price, currency = parse_price(dig(ad, "price", "displayValue"))
        title = ad.get("title") or ""
        description = _description_text(ad.get("description"))
        floor = parse_floor_value(_param_value(params.get("floor"))) or find_floor_in_text(
            f"{title} {description}"
        )
        rooms_value = _param_value(params.get("rooms"))
        rooms_match = re.search(r"\d+", str(rooms_value or ""))
        location = ad.get("location") or {}
        location_text = ", ".join(
            str(location.get(k)) for k in ("districtName", "cityName", "regionName") if location.get(k)
        )
        price_value = float(price) if isinstance(price, (int, float)) else parse_price(price)[0]
        return ListingData(
            platform=self.name,
            external_id=str(ad["id"]),
            url=str(ad.get("url") or ""),
            title=title,
            description=description,
            location_text=location_text,
            price=price_value,
            currency=currency,
            price_eur=to_eur(price_value, currency, self.settings.eur_ron),
            area_m2=parse_area(_param_value(params.get("area"))),
            rooms=int(rooms_match.group(0)) if rooms_match else None,
            floor=floor,
            balcony=detect_balcony(f"{title} {description}"),
            latitude=dig(ad, "map", "lat"),
            longitude=dig(ad, "map", "lon"),
            publisher_name=dig(ad, "user", "name"),
            publisher_type="agency" if ad.get("isBusiness") else "owner",
            phone=find_phone_in_text(description),
            posted_at=parse_datetime(ad.get("createdTime")),
            photo_urls=[_photo_url(p if isinstance(p, str) else str(p.get("link") or ""))
                        for p in ad.get("photos") or [] if p],
            is_detail=is_detail,
            extra={"last_refresh": ad.get("lastRefreshTime")} if ad.get("lastRefreshTime") else {},
        )

    def parse_search(self, html: str, url: str) -> Iterator[ListingData]:
        state = prerendered_state(html)
        ads = dig(state, "listing", "listing", "ads", default=[]) or []
        for ad in ads:
            if isinstance(ad, dict):
                listing = self._from_ad(ad, is_detail=False)
                if listing:
                    yield listing

    def parse_detail(self, html: str, url: str) -> ListingData:
        state = prerendered_state(html)
        ad = dig(state, "ad", "ad", default=None)
        if isinstance(ad, dict):
            listing = self._from_ad(ad, is_detail=True)
            if listing:
                listing.url = url
                return listing
        return self._from_json_ld(html, url)

    def _from_json_ld(self, html: str, url: str) -> ListingData:
        product = next((b for b in json_ld(html) if b.get("@type") in ("Product", "Offer")), {})
        price, currency = parse_price(dig(product, "offers", "price"))
        currency = dig(product, "offers", "priceCurrency") or currency
        description = str(product.get("description") or "")
        return ListingData(
            platform=self.name,
            external_id=str(product.get("sku") or ""),
            url=url,
            title=str(product.get("name") or ""),
            description=description,
            price=price,
            currency=currency,
            price_eur=to_eur(price, currency, self.settings.eur_ron),
            floor=find_floor_in_text(description),
            balcony=detect_balcony(description),
            photo_urls=[str(u) for u in (product.get("image") or []) if u],
            is_detail=True,
        )
