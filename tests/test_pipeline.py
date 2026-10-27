"""Fluxul complet pe SQLite, cu un adapter și un notificator false (fără rețea)."""

import io
from dataclasses import replace

import pytest
from PIL import Image, ImageDraw

from hometracker.adapters.base import Adapter
from hometracker.config import Settings
from hometracker.models import Alert, Listing, init_db, make_engine
from hometracker.pipeline import Pipeline
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


def make_adapter(name: str, catalog: dict[str, list[ListingData]], details: dict[str, ListingData]):
    class FakeAdapter(Adapter):
        def search(self, url, pages):
            yield from (replace(item) for item in catalog[name])

        def fetch_detail(self, listing):
            return replace(details.get(listing.external_id, listing), is_detail=True)

        def parse_search(self, html, url):
            return iter(())

        def parse_detail(self, html, url):
            raise NotImplementedError

    FakeAdapter.name = name
    return FakeAdapter


PROFILE = Profile.model_validate({
    "name": "test",
    "filters": {"price_eur": {"min": 130000, "max": 165000}, "floors": ["ground", "raised_ground", "1"],
                "text_any": ["gheorgheni"]},
    "preferences": {"balcony": 25, "known_floor": 10},
    "notify": {"min_score": 60, "price_drop_percent": 3, "channels": ["ntfy"]},
    "sources": {"storia": {"url": "https://storia.test"}, "olx": {"url": "https://olx.test"}},
})


def listing(platform, external_id, photos, **kwargs) -> ListingData:
    data = dict(platform=platform, external_id=external_id, url=f"https://{platform}.test/{external_id}",
                title="Apartament 2 camere Gheorgheni", price_eur=150000, area_m2=52, floor="1",
                balcony=True, photo_urls=[f"https://img.test/{p}.jpg" for p in photos])
    data.update(kwargs)
    return ListingData(**data)


@pytest.fixture
def setup(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'db.sqlite'}", data_dir=tmp_path)
    sessions = init_db(make_engine(settings.database_url))
    images = {f"https://img.test/{i}.jpg": picture(i) for i in range(1, 20)}
    catalog = {"storia": [], "olx": []}
    notifier = FakeNotifier()

    def run():
        adapters = {name: make_adapter(name, catalog, {}) for name in catalog}
        return Pipeline(settings, sessions, FakeClient(images), notifier, adapters=adapters).run([PROFILE], 1)

    return catalog, notifier, sessions, run


def test_same_apartment_on_two_platforms_alerts_once(setup):
    catalog, notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    catalog["olx"] = [listing("olx", "o1", [2, 3, 4], price_eur=155000)]
    stats = run()
    assert stats.new == 2
    assert len(notifier.alerts) == 1
    assert notifier.alerts[0].title.startswith("Apartament nou · 150.000 €")
    with sessions() as session:
        props = {row.property_id for row in session.query(Listing).all()}
        assert len(props) == 1  # două poze comune → același apartament


def test_different_apartments_alert_separately(setup):
    catalog, notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3]), listing("storia", "s2", [7, 8, 9])]
    run()
    assert len(notifier.alerts) == 2


def test_single_shared_photo_is_not_enough(setup):
    # Aceeași fațadă de bloc, apartamente diferite (etaj diferit).
    catalog, notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], floor="1")]
    catalog["olx"] = [listing("olx", "o1", [1, 10, 11], floor="ground")]
    run()
    assert len(notifier.alerts) == 2


def test_rerun_does_not_repeat_alerts(setup):
    catalog, notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    run()
    run()
    assert len(notifier.alerts) == 1


def test_price_drop_alerts(setup):
    catalog, notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=160000)]
    run()
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=150000)]
    run()
    assert len(notifier.alerts) == 2
    assert notifier.alerts[1].title.startswith("Preț scăzut cu 6%")
    # O scădere mică nu declanșează nimic.
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=149000)]
    run()
    assert len(notifier.alerts) == 2
    with sessions() as session:
        assert session.query(Alert).filter_by(kind="price_drop").count() == 1


def test_non_matching_listing_is_stored_without_alert(setup):
    catalog, notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2], floor="4")]
    run()
    assert notifier.alerts == []
    with sessions() as session:
        row = session.query(Listing).one()
        assert row.property_id is None and row.photos == []


def test_empty_source_sends_one_warning(setup):
    catalog, notifier, sessions, run = setup
    run()
    run()
    warnings = [m for m in notifier.sent if "problemă" in m.title]
    assert len(warnings) == 2  # câte una pe platformă, nu repetată la a doua rulare


def test_price_dropping_into_range_alerts_as_new(setup):
    catalog, notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=175000)]
    run()
    assert notifier.alerts == []
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=162000)]
    run()
    assert len(notifier.alerts) == 1
    assert notifier.alerts[0].title.startswith("Apartament nou · 162.000 €")
