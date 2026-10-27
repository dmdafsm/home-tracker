"""Registrul adapterelor. O platformă nouă = un fișier nou + o linie aici."""

from __future__ import annotations

from .base import Adapter
from .imobiliare import ImobiliareAdapter
from .olx import OlxAdapter
from .storia import StoriaAdapter

ADAPTERS: dict[str, type[Adapter]] = {
    StoriaAdapter.name: StoriaAdapter,
    OlxAdapter.name: OlxAdapter,
    ImobiliareAdapter.name: ImobiliareAdapter,
}

__all__ = ["ADAPTERS", "Adapter"]
