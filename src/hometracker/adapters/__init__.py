"""Registrul adapterelor. O platformă nouă = un fișier nou + o linie aici."""

from __future__ import annotations

from .base import Adapter, ListingRemoved
from .imobiliare import ImobiliareAdapter
from .lajumate import LajumateAdapter
from .olx import OlxAdapter
from .publi24 import Publi24Adapter
from .storia import StoriaAdapter

ADAPTERS: dict[str, type[Adapter]] = {
    StoriaAdapter.name: StoriaAdapter,
    OlxAdapter.name: OlxAdapter,
    ImobiliareAdapter.name: ImobiliareAdapter,
    Publi24Adapter.name: Publi24Adapter,
    LajumateAdapter.name: LajumateAdapter,
}

__all__ = ["ADAPTERS", "Adapter", "ListingRemoved"]
