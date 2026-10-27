"""Ajutoare comune pentru testele fluxului complet: imagini sintetice, adapter și notificator false."""

import io
from dataclasses import replace

from PIL import Image, ImageDraw

from hometracker.adapters.base import Adapter
from hometracker.profiles import Profile
from hometracker.schema import ListingData


def picture(seed: int) -> bytes:
    """Imagini diferite între ele, dar stabile pentru aceeași valoare."""
    image = Image.new("RGB", (320, 240), (240, 240, 240))
    draw = ImageDraw.Draw(image)
    for i in range(6):
        x = (seed * 37 + i * 53) % 280
        y = (seed * 91 + i * 29) % 200
        draw.rectangle([x, y, x + 40 + seed % 30, y + 30], fill=((seed * 50 + i * 40) % 255, 60, 120))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeClient:
    def __init__(self, images: dict[str, bytes]):
        self.images = images

    def get_bytes(self, url: str) -> bytes:
        return self.images[url]


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, message, channels):
        self.sent.append(message)
        return channels

    @property
    def alerts(self):
        """Doar alertele despre apartamente, fără avertismentele tehnice."""
        return [m for m in self.sent if "warning" not in m.tags]


def make_adapter(name: str, catalog: dict[str, list[ListingData]], details: dict, calls: list[str] | None = None):
    """`details` poate conține, pe lângă ListingData, o excepție pe care fetch_detail o aruncă."""

    class FakeAdapter(Adapter):
        def search(self, url, pages):
            yield from (replace(item) for item in catalog[name])

        def fetch_detail(self, listing):
            if calls is not None:
                calls.append(listing.external_id)
            detail = details.get(listing.external_id, listing)
            if isinstance(detail, Exception):
                raise detail
            return replace(detail, is_detail=True)

        def parse_search(self, html, url):
            return iter(())

        def parse_detail(self, html, url):
            raise NotImplementedError

    FakeAdapter.name = name
    return FakeAdapter


PROFILE = Profile.model_validate(
    {
        "name": "test",
        "filters": {
            "price_eur": {"min": 130000, "max": 165000},
            "floors": ["ground", "raised_ground", "1"],
            "text_any": ["gheorgheni"],
        },
        "preferences": {"balcony": 25, "known_floor": 10},
        # Testele vechi de scădere de preț folosesc "alerted"; "saved" e testat separat (test_labels.py).
        "notify": {"min_score": 60, "price_drop_percent": 3, "channels": ["ntfy"], "price_drop_scope": "alerted"},
        "sources": {"storia": {"url": "https://storia.test"}, "olx": {"url": "https://olx.test"}},
    }
)


def listing(platform, external_id, photos, **kwargs) -> ListingData:
    data = dict(
        platform=platform,
        external_id=external_id,
        url=f"https://{platform}.test/{external_id}",
        title="Apartament 2 camere Gheorgheni",
        price_eur=150000,
        area_m2=52,
        floor="1",
        balcony=True,
        photo_urls=[f"https://img.test/{p}.jpg" for p in photos],
    )
    data.update(kwargs)
    return ListingData(**data)
