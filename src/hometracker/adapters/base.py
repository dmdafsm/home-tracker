"""Interfața comună a adapterelor și utilitare pentru paginare și JSON-ul din pagini."""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from ..config import Settings
from ..http import PoliteClient
from ..schema import ListingData

log = logging.getLogger(__name__)


class Adapter(ABC):
    name: str = ""
    page_param: str = "page"

    def __init__(self, client: PoliteClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    def search(self, url: str, pages: int) -> Iterator[ListingData]:
        """Parcurge paginile de rezultate, oprindu-se la prima pagină goală."""
        seen: set[str] = set()
        for page in range(1, pages + 1):
            page_url = with_query_param(url, self.page_param, page) if page > 1 else url
            response = self.client.get(page_url)
            items = list(self.parse_search(response.text, page_url))
            fresh = [item for item in items if item.external_id not in seen]
            log.info("%s: pagina %d → %d anunțuri", self.name, page, len(fresh))
            if not fresh:
                break
            for item in fresh:
                seen.add(item.external_id)
                yield item

    def fetch_detail(self, listing: ListingData) -> ListingData:
        response = self.client.get(listing.url)
        detail = self.parse_detail(response.text, listing.url)
        return merge(listing, detail)

    @abstractmethod
    def parse_search(self, html: str, url: str) -> Iterator[ListingData]:
        ...

    @abstractmethod
    def parse_detail(self, html: str, url: str) -> ListingData:
        ...


def merge(base: ListingData, detail: ListingData) -> ListingData:
    """Completează datele din căutare cu cele din pagina de detaliu (detaliul are prioritate)."""
    for field_name in base.__dataclass_fields__:
        value = getattr(detail, field_name)
        if field_name in ("platform", "external_id", "url"):
            continue
        if field_name == "extra":
            base.extra = {**base.extra, **value}
        elif field_name == "photo_urls":
            if value:
                base.photo_urls = value
        elif value not in (None, "", []):
            setattr(base, field_name, value)
    base.is_detail = True
    return base


def with_query_param(url: str, key: str, value: object) -> str:
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    query.append((key, str(value)))
    return urlunparse(parts._replace(query=urlencode(query, safe=":[]")))


def next_data(html: str) -> dict | None:
    """JSON-ul `__NEXT_DATA__` din site-urile Next.js (de exemplu Storia)."""
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except json.JSONDecodeError:
        return None


def json_ld(html: str) -> list[dict]:
    """Toate blocurile schema.org JSON-LD din pagină, aplatizate."""
    soup = BeautifulSoup(html, "lxml")
    blocks: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if "@graph" in item:
                    stack.extend(item["@graph"])
                blocks.append(item)
    return blocks


def dig(data: object, *path: object, default: object = None) -> object:
    """Acces sigur în structuri imbricate: dig(d, 'a', 0, 'b')."""
    current = data
    for key in path:
        if isinstance(current, dict) and isinstance(key, str):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int) and -len(current) <= key < len(current):
            current = current[key]
        else:
            return default
        if current is None:
            return default
    return current


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    text = re.sub(r"\.\d+(?=[+-]|$)", "", text)  # fără fracțiuni de secundă
    for candidate in (text, text.replace(" ", "T")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def find_objects_with_key(data: object, key: str) -> Iterator[dict]:
    """Toate dicționarele dintr-o structură care conțin o anumită cheie (căutare în adâncime)."""
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if key in item:
                yield item
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
