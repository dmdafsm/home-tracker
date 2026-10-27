"""Formatul comun în care fiecare adapter întoarce un anunț."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ListingData:
    platform: str
    external_id: str
    url: str
    title: str = ""
    description: str = ""
    location_text: str = ""
    price: float | None = None
    currency: str | None = None
    price_eur: float | None = None
    area_m2: float | None = None
    rooms: int | None = None
    floor: str | None = None
    balcony: bool | None = None
    latitude: float | None = None
    longitude: float | None = None
    publisher_name: str | None = None
    publisher_type: str | None = None  # owner / agency / unknown
    phone: str | None = None  # doar în memorie; în baza de date ajunge ca hash
    posted_at: datetime | None = None
    photo_urls: list[str] = field(default_factory=list)
    # True când datele vin dintr-o pagină de detaliu (descriere completă, caracteristici).
    is_detail: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def search_text(self) -> str:
        return " ".join(p for p in (self.title, self.location_text, self.description) if p)
