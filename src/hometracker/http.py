"""Client HTTP politicos: o cerere pe rând per site, pauze aleatoare, arhivarea paginilor brute."""

from __future__ import annotations

import gzip
import hashlib
import logging
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import Settings

log = logging.getLogger(__name__)


class BlockedError(RuntimeError):
    """Site-ul a răspuns cu o pagină de verificare sau a refuzat cererea. Nu încercăm să ocolim."""


class PoliteClient:
    def __init__(self, settings: Settings, *, sleep=time.sleep) -> None:
        self.settings = settings
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "User-Agent": settings.user_agent,
                "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.6",
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _wait_turn(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            delay = random.uniform(self.settings.request_delay_min, self.settings.request_delay_max)
            remaining = last + delay - time.monotonic()
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[host] = time.monotonic()

    def get(
        self,
        url: str,
        *,
        archive: bool = True,
        accept_json: bool = False,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        host = urlparse(url).netloc
        headers = {**({"Accept": "application/json"} if accept_json else {}), **(headers or {})} or None
        backoff = 30.0
        for attempt in range(3):
            self._wait_turn(host)
            try:
                response = self._client.get(url, headers=headers, **({"timeout": timeout} if timeout else {}))
            except httpx.TransportError as exc:
                log.warning("eroare de rețea la %s: %s", url, exc)
                if attempt == 2:
                    raise
                self._sleep(backoff)
                backoff *= 2
                continue
            if response.status_code in (429, 502, 503, 504):
                log.warning("%s a răspuns %s, aștept %.0fs", host, response.status_code, backoff)
                self._sleep(backoff)
                backoff *= 2
                continue
            if response.status_code == 403 or _looks_like_challenge(response):
                raise BlockedError(f"{host} a cerut verificare (HTTP {response.status_code})")
            response.raise_for_status()
            if archive and not accept_json:
                self._archive(url, response.text)
            return response
        raise BlockedError(f"{host} limitează cererile; încerc la următoarea rulare")

    def get_bytes(self, url: str) -> bytes:
        host = urlparse(url).netloc
        self._wait_turn(host)
        response = self._client.get(url, headers={"Accept": "image/*"})
        response.raise_for_status()
        return response.content

    def _archive(self, url: str, body: str) -> None:
        """Salvează HTML-ul brut, ca parserele să poată fi rulate din nou pe istoricul vechi."""
        try:
            day = datetime.now(UTC).strftime("%Y-%m-%d")
            host = urlparse(url).netloc.replace(":", "_")
            digest = hashlib.sha1(url.encode()).hexdigest()[:16]
            path: Path = self.settings.raw_dir / host / day / f"{digest}.html.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                fh.write(f"<!-- {url} -->\n")
                fh.write(body)
        except OSError as exc:  # arhiva e utilă, nu critică
            log.warning("nu am putut arhiva %s: %s", url, exc)


def _looks_like_challenge(response: httpx.Response) -> bool:
    if "text/html" not in response.headers.get("content-type", ""):
        return False
    head = response.text[:5000].lower()
    markers = ("cf-chl-", "challenge-platform", "captcha-delivery", "px-captcha", "are you a robot")
    return any(marker in head for marker in markers)
