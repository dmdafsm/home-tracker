from pathlib import Path

from hometracker.profiles import Profile, load_profiles, match
from hometracker.schema import ListingData

PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"


def make(**kwargs) -> ListingData:
    base = dict(
        platform="storia",
        external_id="1",
        url="https://example.test/1",
        title="Apartament 2 camere Gheorgheni",
        price_eur=148000,
        area_m2=52,
        floor="1",
        is_detail=True,
    )
    base.update(kwargs)
    return ListingData(**base)


def profile() -> Profile:
    return load_profiles(PROFILES_DIR)[0]


def test_bundled_profile_loads():
    p = profile()
    assert p.name == "gheorgheni-mama"
    assert p.filters.floors == ["ground", "raised_ground", "1"]
    assert set(p.sources) == {"storia", "olx", "imobiliare", "publi24", "lajumate"}
    # OLX și Imobiliare.ro blochează colectorul (HTTP 403); rămân dezactivate până există o cale permisă.
    assert {name for name, source in p.sources.items() if source.enabled} == {"storia", "publi24", "lajumate"}


def test_first_floor_with_balcony_scores_full():
    result = match(profile(), make(balcony=True))
    assert result.passed
    assert result.score == 100


def test_upper_floor_rejected():
    result = match(profile(), make(floor="4"))
    assert not result.passed
    assert "etaj 4" in result.reasons[0]


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
    # „boxă la demisol” nu trebuie să excludă un apartament de la etajul 1.
    assert match(profile(), make(description="boxă la demisol")).passed


def test_integer_floor_in_yaml_is_accepted():
    p = Profile.model_validate({"name": "t", "filters": {"floors": [1, "ground"]}})
    assert p.filters.floors == ["1", "ground"]
