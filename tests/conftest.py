"""Fixture-ul `setup`: pipeline-ul pe SQLite temporar, cu adapter și client false."""

from dataclasses import replace

import pytest
from helpers import PROFILE, FakeClient, make_adapter, picture

from hometracker.config import Settings
from hometracker.models import init_db, make_engine
from hometracker.pipeline import Pipeline


@pytest.fixture
def setup(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'db.sqlite'}", data_dir=tmp_path, osm_lookups_per_run=0)
    sessions = init_db(make_engine(settings.database_url))
    images = {f"https://img.test/{i}.jpg": picture(i) for i in range(1, 20)}
    catalog = {"storia": [], "olx": []}

    details: dict = {}
    calls: list[str] = []

    def run(**overrides):
        adapters = {name: make_adapter(name, catalog, details, calls) for name in catalog}
        run_settings = replace(settings, **overrides)
        return Pipeline(run_settings, sessions, FakeClient(images), adapters=adapters).run([PROFILE], 1)

    # Detaliile și apelurile la fetch_detail, pentru testele de reverificare.
    run.details = details
    run.calls = calls
    return catalog, sessions, run
