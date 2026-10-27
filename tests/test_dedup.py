"""Deduplicarea combinată: ce leagă două anunțuri de același apartament și ce le ține separate."""

from dataclasses import replace

from helpers import listing

from hometracker.dedup import (
    COMMON_SHINGLE_LISTINGS,
    MATCH_THRESHOLD,
    Candidate,
    same_photo,
    score_pair,
    shingles,
)
from hometracker.models import Listing

OWNER_TEXT = (
    "Vand apartament 2 camere in Gheorgheni, bloc de caramida, etaj 1 din 4, 52 mp utili, "
    "balcon inchis, centrala proprie, aproape de Iulius Mall si de parcul Primaverii, liber imediat."
)
# O agenție care rescrie anunțul proprietarului păstrează fragmente: blocul, suprafața, reperele.
AGENCY_TEXT = (
    "Oferim spre vanzare apartament 2 camere in Gheorgheni, bloc de caramida, etaj 1 din 4, 52 mp utili, "
    "balcon inchis, centrala proprie, aproape de Iulius Mall. Comision 2%. Sunati acum!"
)
OTHER_TEXT = (
    "Apartament luminos cu doua camere decomandate, renovat complet in 2024, mobilat si utilat, "
    "parcare in curte, zona linistita langa scoala si magazine, disponibil din decembrie."
)
TEMPLATE = (
    "Agentia Exemplu va ofera consultanta gratuita, acte verificate, credit ipotecar prin partenerii nostri, "
    "vizionari zilnice inclusiv in weekend, sunati acum pentru detalii suplimentare. "
)


def properties(sessions) -> dict[str, int]:
    with sessions() as session:
        return {row.external_id: row.property_id for row in session.query(Listing).all()}


def run_one_by_one(catalog, run, *items):
    for item in items:
        catalog[item.platform] = [item]
        run()
    catalog["storia"], catalog["olx"] = [], []


# ----------------------------------------------------------------- semnale


def test_same_photo_accepts_small_changes_confirmed_by_dhash():
    base = ("0" * 16, "0" * 16)
    ten_bits = "3ff" + "0" * 13  # 10 biți diferiți
    assert same_photo(base, ("0f" + "0" * 14, "0" * 16))  # 4 biți: aceeași poză
    assert same_photo(base, (ten_bits, "1" + "0" * 15))  # pHash mai departe, dHash aproape
    assert not same_photo(base, (ten_bits, "fff" + "0" * 13))  # pHash și dHash departe (12 biți)
    assert not same_photo(base, (ten_bits, None))


def test_shingles_ignore_case_diacritics_and_spacing():
    assert shingles("Apartament 52mp în Gheorgheni, bloc de cărămidă, etaj 1/4, lângă Iulius Mall") == shingles(
        "APARTAMENT 52 mp in gheorgheni bloc de caramida etaj 1 4 langa iulius mall"
    )
    assert shingles("prea scurt") == frozenset()


def candidate(**fields) -> Candidate:
    values = dict(
        listing_id=1,
        property_id=1,
        area_m2=None,
        floor=None,
        rooms=None,
        latitude=None,
        longitude=None,
        phone_hash=None,
        is_agency=False,
        photos=(),
        shingles=frozenset(),
    )
    values.update(fields)
    return Candidate(**values)


def test_phone_and_attributes_alone_stay_under_the_threshold():
    a = candidate(phone_hash="h", area_m2=52, floor="1", rooms=2, latitude=46.77, longitude=23.62)
    b = candidate(listing_id=2, phone_hash="h", area_m2=53, floor="1", rooms=2, latitude=46.7705, longitude=23.6205)
    score, reasons, _ = score_pair(a, b, set())
    assert reasons == ["phone", "area", "floor", "rooms", "near"]
    assert score < MATCH_THRESHOLD  # un agent cu mai multe apartamente în același bloc


def test_known_contradictions_veto_the_pair():
    photo = (("0" * 16, "0" * 16), ("f" * 8 + "0" * 8, "0" * 16))
    a = candidate(photos=photo, area_m2=52, floor="1", rooms=2, latitude=46.77, longitude=23.62)
    assert score_pair(a, replace(a, listing_id=2), set())[0] == 1.0
    for change in ({"area_m2": 64}, {"floor": "2"}, {"rooms": 3}, {"latitude": 46.79}):
        assert score_pair(a, replace(a, listing_id=2, **change), set()) is None, change


# ------------------------------------------------------- prin pipeline


def test_two_shared_photos_merge_without_any_attribute(setup):
    catalog, sessions, run = setup
    run_one_by_one(
        catalog,
        run,
        listing("storia", "s1", [1, 2, 3], area_m2=None, floor=None),
        listing("olx", "o1", [2, 3, 9], area_m2=None, floor=None),
    )
    found = properties(sessions)
    assert found["s1"] == found["o1"]


def test_one_shared_photo_needs_area_and_floor(setup):
    catalog, sessions, run = setup
    run_one_by_one(
        catalog,
        run,
        listing("storia", "s1", [1, 2, 3]),
        listing("olx", "o1", [3, 7, 8]),  # aceeași suprafață și același etaj
        listing("olx", "o2", [1, 10, 11], area_m2=None),  # doar poza fațadei
    )
    found = properties(sessions)
    assert found["o1"] == found["s1"]
    assert found["o2"] != found["s1"]


HERE = {"latitude": 46.7700, "longitude": 23.6200}
NEXT_DOOR = {"latitude": 46.7705, "longitude": 23.6204}  # ~60 m


def test_rewritten_text_merges_without_photos_when_the_location_confirms(setup):
    catalog, sessions, run = setup
    run_one_by_one(
        catalog,
        run,
        listing("storia", "s1", [], description=OWNER_TEXT, rooms=2, **HERE),
        listing("olx", "o1", [], description=AGENCY_TEXT, rooms=2, **NEXT_DOOR),
        listing("olx", "o2", [], description=OTHER_TEXT, rooms=2, **NEXT_DOOR),
    )
    found = properties(sessions)
    assert found["o1"] == found["s1"]
    assert found["o2"] != found["s1"]
    with sessions() as session:
        row = session.query(Listing).filter_by(external_id="o1").one()
        assert row.match_signals.startswith("text:") and row.match_signals.endswith("area,floor,rooms,near")


def test_rewritten_text_alone_is_not_enough(setup):
    catalog, sessions, run = setup
    run_one_by_one(
        catalog,
        run,
        listing("storia", "s1", [], description=OWNER_TEXT, rooms=2),
        listing("olx", "o1", [], description=AGENCY_TEXT, rooms=2),  # fără poze, telefon sau coordonate
    )
    found = properties(sessions)
    assert found["o1"] != found["s1"]


def test_similar_text_on_another_floor_stays_separate(setup):
    catalog, sessions, run = setup
    run_one_by_one(
        catalog,
        run,
        listing("storia", "s1", [], description=OWNER_TEXT, rooms=2, **HERE),
        listing("olx", "o1", [], description=AGENCY_TEXT, rooms=2, floor="ground", **NEXT_DOOR),
    )
    found = properties(sessions)
    assert found["o1"] != found["s1"]


def test_agency_template_text_does_not_merge_different_apartments(setup):
    catalog, sessions, run = setup
    # Același șablon la mai multe anunțuri decât COMMON_SHINGLE_LISTINGS, cu același tip de apartament.
    items = [
        listing("storia", f"s{i}", [], description=TEMPLATE + f"Apartament numarul {i}.", rooms=2)
        for i in range(COMMON_SHINGLE_LISTINGS + 2)
    ]
    run_one_by_one(catalog, run, *items)
    assert len(set(properties(sessions).values())) == len(items)


def test_agency_phone_does_not_identify_an_apartment(setup):
    catalog, sessions, run = setup
    agency = {"publisher_type": "agency", "phone": "0722 000 111", "rooms": 2}
    run_one_by_one(
        catalog,
        run,
        listing("storia", "s1", [], description=TEMPLATE + "Apartament A, renovat.", **agency),
        listing("storia", "s2", [], description=TEMPLATE + "Apartament B, nerenovat.", **agency),
    )
    found = properties(sessions)
    assert found["s1"] != found["s2"]


def test_owner_phone_confirms_a_rewritten_text(setup):
    catalog, sessions, run = setup
    owner = {"publisher_type": "owner", "phone": "0722 000 222", "rooms": 2}
    run_one_by_one(
        catalog,
        run,
        listing("storia", "s1", [], description=OWNER_TEXT, **owner),
        listing("olx", "o1", [], description=AGENCY_TEXT, **owner),
    )
    found = properties(sessions)
    assert found["s1"] == found["o1"]


def test_shared_photos_far_apart_stay_separate(setup):
    catalog, sessions, run = setup
    run_one_by_one(
        catalog,
        run,
        listing("storia", "s1", [1, 2, 3], latitude=46.770, longitude=23.620),
        listing("olx", "o1", [1, 2, 3], latitude=46.790, longitude=23.620),  # ~2,2 km: poze de catalog
    )
    found = properties(sessions)
    assert found["o1"] != found["s1"]


def test_best_scoring_apartment_wins(setup):
    catalog, sessions, run = setup
    run_one_by_one(
        catalog,
        run,
        listing("storia", "s1", [1, 2, 3]),
        listing("storia", "s2", [4, 5, 6], description=OWNER_TEXT),
        listing("olx", "o1", [3, 4, 5], description=AGENCY_TEXT),  # o poză din s1, două din s2 + text
    )
    with sessions() as session:
        rows = {row.external_id: row for row in session.query(Listing).all()}
        assert rows["o1"].property_id == rows["s2"].property_id
        assert rows["o1"].matched_listing_id == rows["s2"].id
