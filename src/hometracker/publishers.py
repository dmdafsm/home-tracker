"""Publicatorii: cine publică ce apartamente, câte a preluat de la alții și la ce prețuri."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Listing, Publisher, as_utc, utcnow

# Un „proprietar” al cărui telefon apare la atâtea apartamente diferite e probabil un agent (planul, etapa 3).
DISGUISED_AGENT_PROPERTIES = 5


def publisher_key(listing: Listing) -> str | None:
    """Cine a publicat anunțul: telefonul (hash) sau numele normalizat; None dacă nu se știe."""
    if listing.phone_hash:
        return f"phone:{listing.phone_hash}"
    name = " ".join((listing.publisher_name or "").lower().split())
    return f"name:{name}" if name else None


def link_publisher(session: Session, listing: Listing) -> Publisher | None:
    """Leagă anunțul de publicatorul lui, creându-l la prima apariție."""
    key = publisher_key(listing)
    if key is None:
        listing.publisher_id = None
        return None
    publisher = session.scalar(select(Publisher).where(Publisher.key == key))
    if publisher is None:
        publisher = Publisher(key=key, phone_hash=listing.phone_hash, first_seen=listing.first_seen or utcnow())
        session.add(publisher)
        session.flush()
    if listing.publisher_name and not publisher.name:
        publisher.name = listing.publisher_name
    # „agency” câștigă: un publicator care s-a declarat o dată agenție nu mai e tratat ca proprietar.
    if listing.publisher_type in ("owner", "agency") and publisher.declared_type != "agency":
        publisher.declared_type = listing.publisher_type
    publisher.last_seen = utcnow()
    listing.publisher_id = publisher.id
    return publisher


def link_missing_publishers(session: Session) -> int:
    """Anunțurile salvate înainte de tabela publisher (sau fără publicator încă)."""
    pending = session.scalars(
        select(Listing).where(
            Listing.publisher_id.is_(None),
            (Listing.phone_hash.is_not(None)) | (Listing.publisher_name.is_not(None)),
        )
    ).all()
    linked = sum(1 for listing in pending if link_publisher(session, listing) is not None)
    session.flush()
    return linked


@dataclass
class PublisherStats:
    publisher: Publisher
    listings: list[Listing] = field(default_factory=list)
    taken_over: list[Listing] = field(default_factory=list)  # apartamentul apăruse întâi la alt publicator
    price_differences: list[float] = field(default_factory=list)  # prețul lui minus cel mai mic al altora

    @property
    def active(self) -> list[Listing]:
        return [listing for listing in self.listings if listing.active]

    @property
    def properties(self) -> set[int]:
        return {listing.property_id for listing in self.listings if listing.property_id is not None}

    @property
    def platforms(self) -> list[str]:
        return sorted({listing.platform for listing in self.listings})

    @property
    def taken_over_share(self) -> float | None:
        grouped = [listing for listing in self.listings if listing.property_id is not None]
        return len(self.taken_over) / len(grouped) if grouped else None

    @property
    def average_price_difference(self) -> float | None:
        return mean(self.price_differences) if self.price_differences else None

    @property
    def disguised_agent(self) -> bool:
        return self.publisher.declared_type == "owner" and len(self.properties) >= DISGUISED_AGENT_PROPERTIES


def publisher_stats(session: Session, publisher_id: int | None = None) -> list[PublisherStats]:
    """Statisticile tuturor publicatorilor (sau ale unuia), cu cei mai activi primii."""
    query = select(Publisher)
    if publisher_id is not None:
        query = query.where(Publisher.id == publisher_id)
    publishers = {publisher.id: PublisherStats(publisher) for publisher in session.scalars(query)}
    if not publishers:
        return []
    own = session.scalars(select(Listing).where(Listing.publisher_id.in_(publishers))).all()
    for listing in own:
        publishers[listing.publisher_id].listings.append(listing)

    # Restul anunțurilor din aceleași apartamente: prima apariție și prețurile celorlalți publicatori.
    property_ids = {listing.property_id for listing in own if listing.property_id is not None}
    by_property: dict[int, list[Listing]] = defaultdict(list)
    if property_ids:
        for listing in session.scalars(select(Listing).where(Listing.property_id.in_(property_ids))):
            by_property[listing.property_id].append(listing)
    for listing in own:
        siblings = by_property.get(listing.property_id, [])
        if not siblings:
            continue
        first = min(siblings, key=lambda item: (as_utc(item.first_seen), item.id))
        stats = publishers[listing.publisher_id]
        if first.id != listing.id and first.publisher_id != listing.publisher_id:
            stats.taken_over.append(listing)
        others = [
            item.price_eur
            for item in siblings
            if item.publisher_id != listing.publisher_id and item.price_eur is not None and item.active
        ]
        if others and listing.price_eur is not None and listing.active:
            stats.price_differences.append(listing.price_eur - min(others))
    return sorted(publishers.values(), key=lambda s: (-len(s.active), -len(s.listings), s.publisher.id))
