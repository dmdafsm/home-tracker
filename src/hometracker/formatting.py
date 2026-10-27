"""Formatarea valorilor pentru interfață și linia de comandă: platforme, etaje, prețuri."""

from __future__ import annotations

PLATFORM_NAMES = {
    "storia": "Storia",
    "olx": "OLX",
    "imobiliare": "Imobiliare.ro",
    "publi24": "Publi24",
    "lajumate": "Lajumate",
}
FLOOR_NAMES = {"basement": "demisol", "ground": "parter", "raised_ground": "parter înalt", "attic": "mansardă"}


def floor_label(floor: str | None) -> str:
    if floor is None:
        return "etaj necunoscut"
    return FLOOR_NAMES.get(floor, f"etaj {floor}")


def format_price(value: float | None) -> str:
    if value is None:
        return "preț necunoscut"
    return f"{value:,.0f} €".replace(",", ".")
