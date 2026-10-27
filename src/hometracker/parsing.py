"""Parsare pentru textele în română din anunțuri: etaj, preț, suprafață, balcon."""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Etajele sunt normalizate la: basement, ground, raised_ground, "1", "2", ..., attic.
BASEMENT = "basement"
GROUND = "ground"
RAISED_GROUND = "raised_ground"
ATTIC = "attic"

_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}


def normalize(text: str | None) -> str:
    """Litere mici, fără diacritice, spații simple."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped.lower()).strip()


def parse_floor_value(value: object) -> str | None:
    """Interpretează valoarea unui câmp dedicat de etaj (din API sau din tabelul de caracteristici)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return GROUND if value == 0 else (BASEMENT if value < 0 else str(value))
    text = normalize(str(value)).replace("_", " ").replace("-", " ")
    if not text:
        return None
    if re.search(r"parter (inalt|ridicat)|^p inalt|high ground|raised", text):
        return RAISED_GROUND
    if re.search(r"demisol|subsol|cellar|basement|souterrain", text):
        return BASEMENT
    if re.search(r"mansarda|garret|attic|\bpod\b", text):
        return ATTIC
    if re.fullmatch(r"(parter|p|ground|ground floor|0|floor 0)", text):
        return GROUND
    if text.startswith("parter"):
        return GROUND
    if re.search(r"higher 10|peste 10|> ?10", text):
        return "11"
    for word, number in _ORDINALS.items():
        if re.fullmatch(rf"{word}( floor)?", text):
            return str(number)
    match = re.search(r"(?:floor|etaj(?:ul)?|et\.?)?\s*(\d{1,2})(?:\s*/\s*\d{1,2})?$", text)
    if match:
        number = int(match.group(1))
        return GROUND if number == 0 else str(number)
    return None


_TEXT_FLOOR_PATTERNS: list[tuple[re.Pattern[str], str | None]] = [
    (re.compile(r"\bparter (inalt|ridicat)\b"), RAISED_GROUND),
    (re.compile(r"\bdemisol\b|\bsubsol\b"), BASEMENT),
    (re.compile(r"\b(?:la |situat la |amplasat la )?parter\b"), GROUND),
    (re.compile(r"\bmansarda\b"), ATTIC),
    (re.compile(r"\bet(?:aj(?:ul)?|\.)\s*:?\s*(\d{1,2})\b"), None),  # „etaj 3”, „et. 3”, „Etaj: 3 din 4”
    (re.compile(r"\bet(?:ajul)?\s+(\d{1,2})\s*/\s*\d{1,2}\b"), None),
]


def find_floor_in_text(text: str | None) -> str | None:
    """Caută etajul în text liber (titlu, descriere). Întoarce prima mențiune găsită."""
    norm = normalize(text)
    if not norm:
        return None
    best: tuple[int, str] | None = None
    for pattern, fixed in _TEXT_FLOOR_PATTERNS:
        match = pattern.search(norm)
        if not match:
            continue
        if fixed is None:
            number = int(match.group(1))
            floor = GROUND if number == 0 else str(number)
        else:
            floor = fixed
        if best is None or match.start() < best[0]:
            best = (match.start(), floor)
    return best[1] if best else None


def parse_price(text: object) -> tuple[float | None, str | None]:
    """'150 000 €' -> (150000.0, 'EUR'); '735.000 lei' -> (735000.0, 'RON')."""
    if text is None:
        return None, None
    if isinstance(text, (int, float)):
        return float(text), None
    norm = normalize(str(text))
    currency = None
    if re.search(r"€|eur", norm):
        currency = "EUR"
    elif re.search(r"\blei\b|\bron\b", norm):
        currency = "RON"
    match = re.search(r"\d[\d .,]*", norm)
    if not match:
        return None, currency
    digits = match.group(0).strip()
    # Separatorii de mii: spațiu, punct sau virgulă urmate de exact 3 cifre.
    digits = re.sub(r"[ .,](?=\d{3}(?:\D|$))", "", digits)
    digits = digits.replace(" ", "").replace(",", ".")
    try:
        return float(digits), currency
    except ValueError:
        return None, currency


def to_eur(amount: float | None, currency: str | None, eur_ron: float) -> float | None:
    if amount is None:
        return None
    if currency == "RON":
        return round(amount / eur_ron, 2)
    return amount


def parse_area(text: object) -> float | None:
    """'65 mp', '65,5 m²', '65 m2' -> 65.0 / 65.5. Un număr simplu e acceptat ca atare."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    norm = normalize(str(text)).replace("²", "2")
    match = re.search(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:mp|m2|m\b|metri)", norm)
    if not match:
        match = re.fullmatch(r"\s*(\d{1,4}(?:[.,]\d{1,2})?)\s*", norm)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


_NO_BALCONY = re.compile(r"\b(fara|nu are|lipsa|nu dispune de) (de )?balco")
_BALCONY = re.compile(r"\bbalco(?:n\w*|ane)\b|\blogi[ae]\b|\bterasa\b")


def detect_balcony(text: str | None) -> bool | None:
    """True / False dacă textul spune explicit, None dacă nu se pronunță."""
    norm = normalize(text)
    if not norm:
        return None
    if _NO_BALCONY.search(norm):
        return False
    if _BALCONY.search(norm):
        return True
    return None


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("0040"):
        digits = digits[4:]
    elif digits.startswith("40") and len(digits) == 11:
        digits = digits[2:]
    if not digits.startswith("0"):
        digits = "0" + digits
    return digits if len(digits) == 10 else None


def phone_hash(phone: str | None) -> str | None:
    """Telefonul e păstrat doar ca hash, folosit la potrivirea publicatorilor."""
    normalized = normalize_phone(phone)
    if not normalized:
        return None
    return hashlib.sha256(f"hometracker:{normalized}".encode()).hexdigest()[:32]


_PHONE_IN_TEXT = re.compile(r"(?:\+?40|0040|0)\s?7\d{2}[\s.\-]?\d{3}[\s.\-]?\d{3}")


def find_phone_in_text(text: str | None) -> str | None:
    if not text:
        return None
    match = _PHONE_IN_TEXT.search(text)
    return match.group(0) if match else None
