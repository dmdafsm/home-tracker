"""Descărcarea pozelor și amprentele perceptuale (pHash, dHash)."""

from __future__ import annotations

import hashlib
import io
import logging
import re
from pathlib import Path

import imagehash
from PIL import Image, UnidentifiedImageError

log = logging.getLogger(__name__)


def image_hashes(data: bytes) -> tuple[str, str] | None:
    """(phash, dhash) ca text hex, sau None dacă imaginea nu poate fi citită."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            return str(imagehash.phash(image)), str(imagehash.dhash(image))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        log.debug("imagine ilizibilă: %s", exc)
        return None


def hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def is_informative(phash: str) -> bool:
    """Exclude imaginile aproape uniforme (alb, negru, placeholder), care se potrivesc cu orice."""
    bits = int(phash, 16).bit_count()
    return 6 <= bits <= 58


# Componente de cale acceptate ca atare: id-urile platformelor sunt cifre și litere.
_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9_-]{1,64}")


def safe_component(value: str) -> str:
    """Un nume de director sigur pentru o valoare venită din pagină (de exemplu id-ul extern).

    Valorile obișnuite rămân neschimbate, deci căile existente nu se mută. Orice altceva (`..`, `/`,
    caractere speciale, șir gol sau prea lung) devine un hash stabil, ca să nu poată ieși din director.
    """
    if _SAFE_COMPONENT.fullmatch(value):
        return value
    return "h-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def image_path(directory: Path, platform: str, external_id: str, position: int) -> Path:
    """Unde stă poza unui anunț, calculat doar din câmpurile lui (la fel la salvare și la afișare)."""
    if not _SAFE_COMPONENT.fullmatch(platform):
        raise ValueError(f"nume de platformă nevalid: {platform!r}")
    return directory / platform / safe_component(external_id) / f"{position:02d}.jpg"


def save_image(directory: Path, platform: str, external_id: str, position: int, data: bytes) -> Path:
    path = image_path(directory, platform, external_id, position)
    # A doua barieră, dacă regula de mai sus ar fi slăbită vreodată.
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f"calea pozei iese din {directory}: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
