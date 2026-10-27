import shutil
from pathlib import Path

import yaml

from hometracker.profiles import Profile, load_profiles, match
from hometracker.schema import ListingData

# Singurul profil din repo; profilurile reale din profiles/ sunt locale și nu intră în teste.
EXAMPLE = Path(__file__).resolve().parents[1] / "profiles" / "example.yaml"


def make(**kwargs) -> ListingData:
    base = dict(
        platform="storia",
        external_id="1",
        url="https://example.test/1",
        title="Apartament 2 camere Marasti",
        price_eur=120000,
        area_m2=52,
        floor="2",
        is_detail=True,
    )
    base.update(kwargs)
    return ListingData(**base)


def profile() -> Profile:
    return Profile.model_validate(yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")))


def test_example_profile_is_valid_and_inactive(tmp_path):
    p = profile()
    assert p.name == "example" and not p.active
    assert p.filters.floors == ["1", "2", "3", "4"]
    assert set(p.sources) == {"storia", "olx", "imobiliare", "publi24", "lajumate"}
    # OLX și Imobiliare.ro blochează colectorul (HTTP 403); rămân dezactivate până există o cale permisă.
    assert {name for name, source in p.sources.items() if source.enabled} == {"storia", "publi24", "lajumate"}
    # Inactiv: worker-ul nu colectează pentru exemplu.
    shutil.copy(EXAMPLE, tmp_path)
    assert load_profiles(tmp_path) == []


def test_matching_floor_with_balcony_scores_full():
    result = match(profile(), make(balcony=True))
    assert result.passed
    assert result.score == 100


def test_upper_floor_rejected():
    result = match(profile(), make(floor="5"))
    assert not result.passed
    assert "etaj 5" in result.reasons[0]


def test_price_outside_range_rejected():
    assert not match(profile(), make(price_eur=190000)).passed
    assert not match(profile(), make(price_eur=None)).passed


def test_unknown_floor_passes_with_lower_score():
    known = match(profile(), make(balcony=True))
    unknown = match(profile(), make(balcony=True, floor=None))
    assert unknown.passed
    assert unknown.score < known.score


def test_missing_keyword_in_search_page_asks_for_detail():
    result = match(profile(), make(title="Apartament 2 camere", is_detail=False))
    assert result.passed and result.needs_detail
    assert not match(profile(), make(title="Apartament 2 camere", is_detail=True)).passed


def test_excluded_words_reject():
    assert not match(profile(), make(description="pretabil spațiu comercial")).passed
    # „boxă la demisol” nu trebuie să excludă un apartament de la etajul 2.
    assert match(profile(), make(description="boxă la demisol")).passed


def test_integer_floor_in_yaml_is_accepted():
    p = Profile.model_validate({"name": "t", "filters": {"floors": [1, "ground"]}})
    assert p.filters.floors == ["1", "ground"]
