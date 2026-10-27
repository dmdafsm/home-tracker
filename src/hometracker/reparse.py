"""Re-parsarea paginilor arhivate în `data/raw/`: actualizează descrierea anunțurilor fără cereri noi.

Folosit când un parser se îmbunătățește (de exemplu descrierile Storia, care își pierdeau paragrafele).
Se schimbă doar `description`; celelalte câmpuri rămân cum le-a salvat colectarea.
"""

from __future__ import annotations

import gzip
import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters import ADAPTERS, Adapter
from .config import Settings
from .models import Listing

log = logging.getLogger(__name__)

_HEADER = re.compile(r"<!-- (\S+) -->")


def archived_pages(raw_dir: Path) -> dict[str, Path]:
    """URL -> cea mai nouă pagină arhivată pentru el (prima linie a fișierului e URL-ul)."""
    found: dict[str, tuple[float, Path]] = {}
    for path in raw_dir.glob("*/*/*.html.gz"):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                match = _HEADER.match(fh.readline())
        except (OSError, EOFError, UnicodeDecodeError) as exc:
            log.warning("arhivă ilizibilă %s: %s", path, exc)
            continue
        if match:
            mtime = path.stat().st_mtime
            if match.group(1) not in found or found[match.group(1)][0] < mtime:
                found[match.group(1)] = (mtime, path)
    return {url: path for url, (_mtime, path) in found.items()}


def reparse_descriptions(session: Session, settings: Settings, adapters: dict[str, type[Adapter]] | None = None) -> int:
    """Întoarce câte descrieri s-au schimbat."""
    adapters = adapters or ADAPTERS
    pages = archived_pages(settings.raw_dir)
    if not pages:
        return 0
    changed = 0
    instances: dict[str, Adapter] = {}
    for listing in session.scalars(select(Listing).where(Listing.url.in_(list(pages)))):
        adapter_cls = adapters.get(listing.platform)
        if adapter_cls is None:
            continue
        adapter = instances.setdefault(listing.platform, adapter_cls(None, settings))  # parse_detail nu face cereri
        with gzip.open(pages[listing.url], "rt", encoding="utf-8") as fh:
            page = fh.read()
        try:
            detail = adapter.parse_detail(page, listing.url)
        except Exception as exc:  # o pagină arhivată stricată nu oprește restul
            log.warning("nu am putut re-parsa %s: %s", listing.url, exc)
            continue
        if detail.description and detail.description != listing.description:
            listing.description = detail.description
            changed += 1
    session.flush()
    return changed
