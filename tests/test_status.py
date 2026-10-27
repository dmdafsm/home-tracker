"""Starea colectării: problemele pe platformă din run_log, pagina /status și bannerul din feed."""

from datetime import timedelta

import yaml
from fastapi.testclient import TestClient
from helpers import PROFILE, listing

from hometracker.config import Settings
from hometracker.models import RunLog, utcnow
from hometracker.status import collection_status
from hometracker.web import create_app

DISABLED_OLX = PROFILE.model_copy(
    update={"sources": {**PROFILE.sources, "olx": PROFILE.sources["olx"].model_copy(update={"enabled": False})}}
)


def statuses(sessions, profile=PROFILE, now=None):
    with sessions() as session:
        return {s.platform: s for s in collection_status(session, [profile], Settings(), now=now)}


def test_problems_per_platform(setup):
    catalog, sessions, run = setup
    assert statuses(sessions)["storia"].problem == "încă nicio rulare"
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    run()
    found = statuses(sessions)
    assert found["storia"].problem is None and found["storia"].last_ok is not None
    assert "niciun anunț" in found["olx"].problem
    with sessions() as session:
        session.add(RunLog(profile="test", platform="storia", found=0, new=0, error="storia.ro a cerut verificare"))
        session.commit()
    found = statuses(sessions)
    assert found["storia"].problem == "ultima rulare a eșuat: storia.ro a cerut verificare"
    assert [run.error for run in found["storia"].runs][:1] == ["storia.ro a cerut verificare"]


def test_stale_runs_and_disabled_sources(setup):
    catalog, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    run()
    later = utcnow() + timedelta(hours=3)  # peste două intervale maxime (2 × 60 min)
    assert "nicio rulare recentă" in statuses(sessions, now=later)["storia"].problem
    disabled = statuses(sessions, DISABLED_OLX)["olx"]
    assert not disabled.enabled and disabled.problem is None


def test_status_page_and_feed_banner(setup, tmp_path):
    catalog, sessions, run = setup
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "test.yaml").write_text(yaml.safe_dump(PROFILE.model_dump(mode="json")), encoding="utf-8")
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    run()
    client = TestClient(
        create_app(Settings(database_url="sqlite://", data_dir=tmp_path, profiles_dir=profiles), sessions)
    )
    page = client.get("/status").text
    assert "Starea colectării" in page and "Storia" in page and "niciun anunț" in page
    feed = client.get("/").text
    assert "Colectarea are probleme pe OLX" in feed and 'href="/status"' in feed
    (profiles / "test.yaml").write_text(yaml.safe_dump(DISABLED_OLX.model_dump(mode="json")), encoding="utf-8")
    assert "Colectarea are probleme" not in client.get("/").text
