"""Editarea profilurilor din interfață: citirea fișierelor YAML, validarea și scrierea atomică.

Profilul se editează ca text YAML, ca să rămână comentariile din fișier. Worker-ul citește
profilurile la fiecare rulare, deci o modificare salvată intră în vigoare la rularea următoare.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from .adapters import ADAPTERS
from .profiles import Profile

# Numele profilului e și numele fișierului.
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
MAX_BYTES = 64_000
HISTORY_DIR = ".history"


@dataclass
class ProfileFile:
    path: Path
    text: str
    profile: Profile | None
    error: str | None = None

    @property
    def name(self) -> str:
        return self.profile.name if self.profile else self.path.stem


class ProfileError(ValueError):
    pass


def parse(text: str) -> Profile:
    """Textul YAML -> profil validat, sau ProfileError cu un mesaj de citit în interfață."""
    if len(text.encode()) > MAX_BYTES:
        raise ProfileError(f"profilul are peste {MAX_BYTES // 1000} kB")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProfileError(f"YAML invalid: {exc}") from None
    if not isinstance(data, dict):
        raise ProfileError("profilul trebuie să fie un dicționar YAML (chei: name, filters, preferences, sources …)")
    try:
        profile = Profile.model_validate(data)
    except ValidationError as exc:
        lines = [f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()]
        raise ProfileError("; ".join(lines)) from None
    if not NAME.fullmatch(profile.name):
        raise ProfileError("name: doar litere mici, cifre și - (cel mult 40), de exemplu gheorgheni-mama")
    unknown = sorted(set(profile.sources) - set(ADAPTERS))
    if unknown:
        raise ProfileError(f"sources: platformă necunoscută {', '.join(unknown)} (cunoscute: {', '.join(ADAPTERS)})")
    for platform, source in profile.sources.items():
        if not source.url.startswith(("https://", "http://")):
            raise ProfileError(f"sources.{platform}.url: trebuie să înceapă cu https://")
    return profile


def read_files(directory: Path) -> list[ProfileFile]:
    """Toate fișierele de profil, inclusiv cele inactive sau invalide (cu eroarea lor)."""
    found = []
    for path in sorted(directory.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        try:
            found.append(ProfileFile(path, text, parse(text)))
        except ProfileError as exc:
            found.append(ProfileFile(path, text, None, str(exc)))
    return found


def find(directory: Path, name: str) -> ProfileFile | None:
    return next((item for item in read_files(directory) if item.name == name), None)


def copy_text(source: ProfileFile, new_name: str) -> str:
    """Textul unui profil existent, cu alt nume, pentru „profil nou din acesta”."""
    text, count = re.subn(r"(?m)^name:.*$", f"name: {new_name}", source.text, count=1)
    return text if count else f"name: {new_name}\n{source.text}"


def save(directory: Path, text: str, *, original: ProfileFile | None) -> ProfileFile:
    """Validează și scrie profilul. `original` None = profil nou (fișierul nu trebuie să existe)."""
    text = text.replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    profile = parse(text)
    if original is not None:
        if original.profile is not None and profile.name != original.profile.name:
            # Numele e și numele fișierului; un profil cu alt nume e un fișier nou.
            raise ProfileError("name: numele nu se schimbă la editare; creează un profil nou din acesta")
        path = original.path
    else:
        path = directory / f"{profile.name}.yaml"
        if path.exists() or find(directory, profile.name):
            raise ProfileError(f"name: există deja un profil {profile.name}")
    if path.exists():
        history = directory / HISTORY_DIR
        history.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        (history / f"{path.stem}-{stamp}.yaml").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    # Scriere atomică: worker-ul poate citi fișierul în orice moment.
    fd, temp = tempfile.mkstemp(dir=directory, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return ProfileFile(path, text, profile)
