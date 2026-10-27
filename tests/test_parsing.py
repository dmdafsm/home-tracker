import pytest

from hometracker.parsing import (
    detect_balcony,
    find_floor_in_text,
    normalize,
    parse_area,
    parse_floor_value,
    parse_price,
    phone_hash,
    to_eur,
)


def test_normalize_strips_diacritics_both_forms():
    # ș/ț cu virgulă și ş/ţ cu sedilă trebuie să ajungă la fel.
    assert normalize("Ștefan Țară ŞŢ  înalt") == "stefan tara st inalt"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Parter", "ground"),
        ("parter înalt", "raised_ground"),
        ("P înalt", "raised_ground"),
        ("ground_floor", "ground"),
        ("GROUND", "ground"),
        ("FIRST", "1"),
        ("floor_1", "1"),
        ("Etaj 1", "1"),
        ("1/4", "1"),
        ("3", "3"),
        (0, "ground"),
        ("Demisol", "basement"),
        ("cellar", "basement"),
        ("Mansardă", "attic"),
        ("floor_higher_10", "11"),
        ("", None),
        (None, None),
    ],
)
def test_parse_floor_value(value, expected):
    assert parse_floor_value(value) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Apartament 2 camere, parter înalt, Gheorgheni", "raised_ground"),
        ("Vând ap. 3 cam, et. 1/4, bloc de cărămidă", "1"),
        ("etajul 4 din 4, fără lift", "4"),
        ("Situat la parter, ideal pentru persoane în vârstă", "ground"),
        ("Caracteristici:\n - Etaj: 3 din 4\n - Compartimentare: semidecomandat", "3"),
        ("Apartament luminos cu balcon", None),
    ],
)
def test_find_floor_in_text(text, expected):
    assert find_floor_in_text(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("150 000 €", (150000.0, "EUR")),
        ("149.900 EUR", (149900.0, "EUR")),
        ("735.000 lei", (735000.0, "RON")),
        ("152,500 euro", (152500.0, "EUR")),
        (148000, (148000.0, None)),
        ("Preț la cerere", (None, None)),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


def test_to_eur_converts_ron_only():
    assert to_eur(497000, "RON", 4.97) == 100000.0
    assert to_eur(150000, "EUR", 4.97) == 150000
    assert to_eur(None, "RON", 4.97) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("65 mp", 65.0), ("65,5 m²", 65.5), ("52 m2", 52.0), (48, 48.0), ("48", 48.0), ("mare", None)],
)
def test_parse_area(text, expected):
    assert parse_area(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Apartament cu balcon închis", True),
        ("două balcoane", True),
        ("are logie spațioasă", True),
        ("Fără balcon, dar cu boxă", False),
        ("nu are balcon", False),
        ("Apartament renovat", None),
    ],
)
def test_detect_balcony(text, expected):
    assert detect_balcony(text) is expected


def test_phone_hash_is_stable_across_formats():
    assert phone_hash("0722 123 456") == phone_hash("+40 722-123-456") == phone_hash("0040722123456")
    assert phone_hash("12345") is None
