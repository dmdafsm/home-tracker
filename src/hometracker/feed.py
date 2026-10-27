"""Feed-ul: apartamentele care trec de un profil, cu filtrele și sortările din formularul interfeței."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlencode

from sqlalchemy import select

from .authenticity import Assessment, Context, assess
from .buildyear import YearEstimate, estimate_for
from .formatting import PLATFORM_NAMES
from .history import PropertyHistory, property_history
from .labels import labels_for
from .merge import Suggestion, possible_duplicates
from .models import Listing, Photo, PropertyLabel, as_utc
from .pipeline import listing_to_data
from .profiles import MatchResult, Profile, match

# Câte poze intră în caruselul unui card.
CARD_PHOTOS = 8

SORTS = {
    "score": "potrivire",
    "newest": "cele mai noi",
    "price_asc": "preț crescător",
    "price_desc": "preț descrescător",
    "price_m2": "preț pe mp",
    "area_desc": "suprafață",
    "authenticity": "autenticitate",
}
ROOMS = ("1", "2", "3", "4")  # 4 = 4 sau mai multe
PUBLISHERS = {"owner": "proprietar", "agency": "agenție"}


@dataclass
class FeedItem:
    history: PropertyHistory
    listing: Listing  # anunțul cu scorul cel mai bun pentru profil
    result: MatchResult
    photo_ids: list[int]
    assessment: Assessment
    label: PropertyLabel | None
    year: YearEstimate | None
    duplicates: list[Suggestion] = field(default_factory=list)

    @property
    def platforms(self) -> list[str]:
        return sorted({PLATFORM_NAMES.get(item.platform, item.platform) for item in self.history.listings})

    @property
    def min_price(self) -> float | None:
        prices = self.history.price_range()
        return prices[0] if prices else None

    @property
    def cheapest(self) -> Listing:
        """Anunțul activ cel mai ieftin (butonul „anunț” de pe card)."""
        pool = [item for item in self.history.active or self.history.listings if item.price_eur is not None]
        return min(pool, key=lambda item: item.price_eur) if pool else self.listing

    @property
    def price_per_m2(self) -> float | None:
        price, area = self.min_price, self.listing.area_m2
        return price / area if price and area else None


def _int(value: str | None) -> int | None:
    try:
        return int(str(value).replace(".", "").replace(" ", "")) if value not in (None, "") else None
    except ValueError:
        return None


@dataclass
class FeedFilters:
    """Ce a ales utilizatorul în formular. Valorile goale sau invalide înseamnă „oricare”."""

    q: str = ""
    price_min: int | None = None
    price_max: int | None = None
    area_min: int | None = None
    rooms: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    publisher: str = ""
    saved: bool = False
    hidden: bool = False
    removed: bool = False
    sort: str = "score"

    @classmethod
    def from_query(cls, params: Mapping, getlist) -> FeedFilters:
        return cls(
            q=(params.get("q") or "").strip()[:100],
            price_min=_int(params.get("price_min")),
            price_max=_int(params.get("price_max")),
            area_min=_int(params.get("area_min")),
            rooms=[value for value in getlist("rooms") if value in ROOMS],
            platforms=[value for value in getlist("platform") if value in PLATFORM_NAMES],
            publisher=params.get("publisher") if params.get("publisher") in PUBLISHERS else "",
            saved=params.get("saved") == "1",
            hidden=params.get("hidden") == "1",
            removed=params.get("removed") == "1",
            sort=params.get("sort") if params.get("sort") in SORTS else "score",
        )

    @property
    def active_count(self) -> int:
        """Câte filtre restrâng lista (pentru „șterge filtrele”)."""
        values = [self.q, self.price_min, self.price_max, self.area_min, self.rooms, self.platforms, self.publisher]
        return sum(1 for value in values if value not in ("", None, [])) + self.saved

    def query(self, profile: str, **changes) -> str:
        """Query string-ul pentru aceleași filtre, cu `changes` aplicate (link-uri și „înapoi la feed”)."""
        values = {**self.__dict__, **changes}
        pairs: list[tuple[str, str]] = [("profile", profile)]
        for key in ("q", "price_min", "price_max", "area_min", "publisher"):
            if values[key] not in ("", None):
                pairs.append((key, str(values[key])))
        pairs += [("rooms", value) for value in values["rooms"]]
        pairs += [("platform", value) for value in values["platforms"]]
        pairs += [(key, "1") for key in ("saved", "hidden", "removed") if values[key]]
        if values["sort"] != "score":
            pairs.append(("sort", values["sort"]))
        return urlencode(pairs)

    def keep(self, item: FeedItem) -> bool:
        listings = item.history.active or item.history.listings
        if self.q:
            needle = self.q.lower()
            text = " ".join(f"{x.title} {x.location_text or ''} {x.description or ''}" for x in listings).lower()
            if needle not in text:
                return False
        price = item.min_price
        if self.price_min is not None and (price is None or price < self.price_min):
            return False
        if self.price_max is not None and (price is None or price > self.price_max):
            return False
        if self.area_min is not None and (item.listing.area_m2 is None or item.listing.area_m2 < self.area_min):
            return False
        if self.rooms:
            rooms = item.listing.rooms
            if rooms is None or not any(str(rooms) == r or (r == "4" and rooms >= 4) for r in self.rooms):
                return False
        if self.platforms and not {x.platform for x in listings} & set(self.platforms):
            return False
        return not self.publisher or any(x.publisher_type == self.publisher for x in listings)


def _sort_key(sort: str):
    def newest(item: FeedItem) -> float:
        return -as_utc(item.history.listings[-1].first_seen).timestamp()

    missing = float("inf")  # valorile necunoscute la final, indiferent de ordine
    keys = {
        "score": lambda item: (-item.result.score, newest(item)),
        "newest": lambda item: (newest(item),),
        "price_asc": lambda item: (item.min_price if item.min_price is not None else missing, newest(item)),
        "price_desc": lambda item: (-item.min_price if item.min_price is not None else missing, newest(item)),
        "price_m2": lambda item: (item.price_per_m2 if item.price_per_m2 is not None else missing, newest(item)),
        "area_desc": lambda item: (-(item.listing.area_m2 or 0) if item.listing.area_m2 else missing, newest(item)),
        "authenticity": lambda item: (-item.assessment.score, -item.result.score),
    }
    return keys[sort]


def _photo_ids(session, history: PropertyHistory) -> list[int]:
    return list(
        session.scalars(
            select(Photo.id)
            .where(Photo.listing_id.in_([listing.id for listing in history.listings]), Photo.path.is_not(None))
            .order_by(Photo.listing_id, Photo.position)
            .limit(CARD_PHOTOS)
        )
    )


def feed(session, profile: Profile, filters: FeedFilters | None = None) -> list[FeedItem]:
    """Apartamentele cu cel puțin un anunț care trece de profil, filtrate și sortate după formular."""
    filters = filters or FeedFilters()
    ids = session.scalars(select(Listing.property_id).where(Listing.property_id.is_not(None)).distinct()).all()
    context = Context.build(session)
    labels = labels_for(session, ids)
    duplicates = possible_duplicates(session)
    items = []
    for property_id in ids:
        label = labels.get(property_id)
        if label and label.hidden and not filters.hidden:
            continue
        if filters.saved and not (label and label.saved):
            continue
        history = property_history(session, property_id)
        if history is None or (not history.active and not filters.removed):
            continue
        candidates = history.active or history.listings
        scored = [(listing, match(profile, listing_to_data(listing))) for listing in candidates]
        scored = [(listing, result) for listing, result in scored if result.passed]
        if not scored:
            continue
        listing, result = max(scored, key=lambda pair: pair[1].score)
        item = FeedItem(
            history,
            listing,
            result,
            _photo_ids(session, history),
            assess(history, context),
            label,
            estimate_for(session, history.listings),
            duplicates.get(property_id, []),
        )
        if filters.keep(item):
            items.append(item)
    items.sort(key=_sort_key(filters.sort))
    return items
