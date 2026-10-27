"""Modelul de date: anunțuri (listing) grupate pe apartamente fizice (property)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """SQLite întoarce datele fără fus orar; în baza de date sunt mereu UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class Property(Base):
    """Un apartament fizic. Poate avea multe anunțuri, de pe mai multe platforme."""

    __tablename__ = "property"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listings: Mapped[list[Listing]] = relationship(back_populates="property")


class Publisher(Base):
    """Cine publică: identificat prin telefon (hash) sau, fără telefon, prin numele normalizat."""

    __tablename__ = "publisher"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(160), unique=True)
    name: Mapped[str | None] = mapped_column(String(256))
    declared_type: Mapped[str | None] = mapped_column(String(16))  # ce declară anunțurile: owner / agency
    phone_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Listing(Base):
    """O apariție a unui anunț pe o platformă."""

    __tablename__ = "listing"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_listing_platform_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    location_text: Mapped[str] = mapped_column(Text, default="")

    price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    price_eur: Mapped[float | None] = mapped_column(Float, index=True)
    area_m2: Mapped[float | None] = mapped_column(Float)
    rooms: Mapped[int | None] = mapped_column(Integer)
    floor: Mapped[str | None] = mapped_column(String(16))
    balcony: Mapped[bool | None] = mapped_column(Boolean)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    publisher_name: Mapped[str | None] = mapped_column(String(256))
    publisher_type: Mapped[str | None] = mapped_column(String(16))  # owner / agency / unknown
    phone_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    publisher_id: Mapped[int | None] = mapped_column(Integer, index=True)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    detail_fetched: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Ultima reverificare a paginii de detaliu și momentul în care anunțul a dispărut de pe platformă.
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

    property_id: Mapped[int | None] = mapped_column(ForeignKey("property.id"), index=True)
    # De ce a fost legat de apartament: "new" (apartament nou) sau "score" (scorul combinat din dedup.py,
    # cu semnalele în match_signals, de exemplu "photos:2,text:0.62,area,floor"). Rândurile mai vechi pot
    # avea "photos" sau "photo+attributes". Coloana rămâne scurtă: tipul ei nu se schimbă în bazele existente.
    match_reason: Mapped[str | None] = mapped_column(String(32))
    match_signals: Mapped[str | None] = mapped_column(String(128))
    shared_photos: Mapped[int | None] = mapped_column(Integer)
    match_score: Mapped[float | None] = mapped_column(Float)
    # Anunțul cu care s-a potrivit cel mai bine când a fost legat de apartament.
    matched_listing_id: Mapped[int | None] = mapped_column(Integer)
    # Anunțul anterior al aceluiași publicator, pe aceeași platformă, pentru același apartament.
    republished_from_id: Mapped[int | None] = mapped_column(Integer, index=True)
    property: Mapped[Property | None] = relationship(back_populates="listings")
    photos: Mapped[list[Photo]] = relationship(back_populates="listing", cascade="all, delete-orphan")
    snapshots: Mapped[list[ListingSnapshot]] = relationship(back_populates="listing", cascade="all, delete-orphan")


class ListingSnapshot(Base):
    """O versiune a anunțului, salvată la fiecare schimbare de preț."""

    __tablename__ = "listing_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listing.id"), index=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    price_eur: Mapped[float | None] = mapped_column(Float)

    listing: Mapped[Listing] = relationship(back_populates="snapshots")


class Photo(Base):
    __tablename__ = "photo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listing.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str] = mapped_column(Text)
    path: Mapped[str | None] = mapped_column(Text)
    phash: Mapped[str | None] = mapped_column(String(32), index=True)
    dhash: Mapped[str | None] = mapped_column(String(32))

    listing: Mapped[Listing] = relationship(back_populates="photos")


class RunLog(Base):
    """O rulare a unui adapter; folosită pentru recuperare și pentru a detecta adaptere stricate."""

    __tablename__ = "run_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    profile: Mapped[str] = mapped_column(String(64))
    platform: Mapped[str] = mapped_column(String(32))
    pages: Mapped[int] = mapped_column(Integer, default=0)
    found: Mapped[int] = mapped_column(Integer, default=0)
    new: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class PropertyLabel(Base):
    """Starea pusă de utilizator pe un apartament: salvat, ascuns, verdict după ce a sunat."""

    __tablename__ = "property_label"

    property_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    verdict: Mapped[str | None] = mapped_column(String(16))  # real / fake
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LabelEvent(Base):
    """Fiecare acțiune din interfață, păstrată pentru calibrarea ponderilor (deduplicare, autenticitate)."""

    __tablename__ = "label_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(Integer, index=True)
    listing_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DistinctProperties(Base):
    """Două apartamente despre care utilizatorul a spus că nu sunt același (nu mai sunt sugerate)."""

    __tablename__ = "property_distinct"
    __table_args__ = (UniqueConstraint("property_a", "property_b"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_a: Mapped[int] = mapped_column(Integer, index=True)  # property_a < property_b
    property_b: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OsmBuilding(Base):
    """Rezultatul unei interogări Overpass pentru o locație (inclusiv „nimic găsit”), ca să nu se repete."""

    __tablename__ = "osm_building"
    __table_args__ = (UniqueConstraint("lat_key", "lon_key", name="uq_osm_building_location"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lat_key: Mapped[float] = mapped_column(Float)
    lon_key: Mapped[float] = mapped_column(Float)
    osm_id: Mapped[str | None] = mapped_column(String(32))
    start_date: Mapped[str | None] = mapped_column(String(32))
    levels: Mapped[str | None] = mapped_column(String(16))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def make_engine(url: str) -> Engine:
    return create_engine(url, future=True, pool_pre_ping=True)


def add_missing_columns(engine: Engine) -> list[str]:
    """Adaugă în tabelele existente coloanele noi din modele.

    `create_all` creează doar tabelele lipsă, nu și coloanele noi. Coloanele adăugate ulterior
    trebuie să accepte NULL și să nu aibă valoare implicită în baza de date, ca ALTER TABLE
    să meargă la fel pe PostgreSQL și pe SQLite.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added = []
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                if not column.nullable or column.server_default is not None:
                    raise RuntimeError(f"coloana nouă {table.name}.{column.name} trebuie să accepte NULL")
                column_type = column.type.compile(dialect=engine.dialect)
                connection.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'))
                added.append(f"{table.name}.{column.name}")
    return added


def init_db(engine: Engine) -> sessionmaker:
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    return sessionmaker(engine, expire_on_commit=False, future=True)
