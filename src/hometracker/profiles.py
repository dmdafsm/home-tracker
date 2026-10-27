"""Profiluri de căutare (fișiere YAML) și potrivirea unui anunț cu un profil."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from .parsing import normalize
from .schema import ListingData


class Range(BaseModel):
    min: float | None = None
    max: float | None = None

    def contains(self, value: float | None) -> bool:
        if value is None:
            return True
        if self.min is not None and value < self.min:
            return False
        return self.max is None or value <= self.max


class Filters(BaseModel):
    price_eur: Range = Field(default_factory=Range)
    area_m2: Range = Field(default_factory=Range)
    rooms: Range = Field(default_factory=Range)
    floors: list[str] = Field(default_factory=list)
    allow_unknown_floor: bool = True
    allow_unknown_price: bool = False
    text_any: list[str] = Field(default_factory=list)
    text_none: list[str] = Field(default_factory=list)

    @field_validator("floors", mode="before")
    @classmethod
    def _floors_as_text(cls, value: object) -> object:
        # În YAML, `1` e număr; etajele sunt comparate ca text.
        if isinstance(value, list):
            return [str(item) for item in value]
        return value


class Preferences(BaseModel):
    balcony: float = 0
    known_floor: float = 0


class NotifySettings(BaseModel):
    min_score: float = 60
    price_drop_percent: float = 3
    channels: list[str] = Field(default_factory=lambda: ["ntfy"])


class Source(BaseModel):
    url: str
    enabled: bool = True


class Profile(BaseModel):
    name: str
    description: str = ""
    active: bool = True
    filters: Filters = Field(default_factory=Filters)
    preferences: Preferences = Field(default_factory=Preferences)
    notify: NotifySettings = Field(default_factory=NotifySettings)
    sources: dict[str, Source] = Field(default_factory=dict)


@dataclass
class MatchResult:
    passed: bool
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    # True dacă un filtru nu poate fi decis încă (de ex. lipsește etajul din pagina de căutare).
    needs_detail: bool = False


def load_profiles(directory: Path) -> list[Profile]:
    profiles = []
    for path in sorted(directory.glob("*.y*ml")):
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        profile = Profile.model_validate(data)
        if profile.active:
            profiles.append(profile)
    return profiles


def _floor_label(floor: str) -> str:
    return {
        "basement": "demisol",
        "ground": "parter",
        "raised_ground": "parter înalt",
        "attic": "mansardă",
    }.get(floor, f"etaj {floor}")


def match(profile: Profile, listing: ListingData) -> MatchResult:
    """Aplică filtrele dure, apoi calculează un scor 0–100 din preferințe."""
    f = profile.filters
    reasons: list[str] = []
    needs_detail = False

    if listing.price_eur is None:
        if not f.allow_unknown_price:
            return MatchResult(False, reasons=["fără preț"])
    elif not f.price_eur.contains(listing.price_eur):
        return MatchResult(False, reasons=[f"preț {listing.price_eur:,.0f} € în afara intervalului"])

    if not f.area_m2.contains(listing.area_m2):
        return MatchResult(False, reasons=[f"suprafață {listing.area_m2} mp în afara intervalului"])
    if listing.rooms is not None and not f.rooms.contains(listing.rooms):
        return MatchResult(False, reasons=[f"{listing.rooms} camere în afara intervalului"])

    if f.floors:
        if listing.floor is None:
            if not f.allow_unknown_floor:
                return MatchResult(False, reasons=["etaj necunoscut"])
            needs_detail = not listing.is_detail
            reasons.append("etaj nedeclarat")
        elif listing.floor not in {str(x) for x in f.floors}:
            return MatchResult(False, reasons=[f"{_floor_label(listing.floor)} nu e în listă"])
        else:
            reasons.append(_floor_label(listing.floor))

    text = normalize(listing.search_text)
    if f.text_any and not any(normalize(term) in text for term in f.text_any):
        if listing.is_detail:
            return MatchResult(False, reasons=["niciun cuvânt cheie găsit"])
        needs_detail = True
    for term in f.text_none:
        if normalize(term) in text:
            return MatchResult(False, reasons=[f"conține „{term}”"])

    # Scor: pornește de la baza care rămâne după preferințe, apoi adaugă ce se potrivește.
    p = profile.preferences
    total_weight = p.balcony + p.known_floor
    score = 100.0 - total_weight
    if listing.balcony:
        score += p.balcony
        reasons.append("balcon")
    elif listing.balcony is None:
        score += p.balcony / 2  # neprecizat: jumătate din pondere
    else:
        reasons.append("fără balcon")
    if listing.floor is not None:
        score += p.known_floor

    return MatchResult(True, score=round(max(0.0, min(100.0, score)), 1), reasons=reasons, needs_detail=needs_detail)
