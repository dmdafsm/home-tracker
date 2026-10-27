"""Descărcarea pozelor și amprentele perceptuale (pHash, dHash)."""

from __future__ import annotations

import io
import logging
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


def save_image(directory: Path, platform: str, external_id: str, position: int, data: bytes) -> Path:
    path = directory / platform / external_id / f"{position:02d}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
