"""O rulare: caută pe fiecare platformă, salvează și grupează anunțurile pe apartamente."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from .adapters import ADAPTERS, Adapter, ListingRemoved
from .config import Settings
from .dedup import assign_property
from .formatting import PLATFORM_NAMES
from .http import BlockedError, PoliteClient
from .models import Listing, ListingSnapshot, Photo, RunLog, utcnow
from .osm import lookup as osm_lookup
from .osm import pending_locations
from .parsing import phone_hash
from .photos import image_hashes, save_image
from .profiles import MatchResult, Profile, match
from .publishers import link_missing_publishers, link_publisher
from .schema import ListingData

log = logging.getLogger(__name__)

# Câmpurile copiate din ListingData în rândul din baza de date.
_COPIED_FIELDS = (
    "url",
    "title",
    "description",
    "location_text",
    "price",
    "currency",
    "price_eur",
    "area_m2",
    "rooms",
    "floor",
    "balcony",
    "latitude",
    "longitude",
    "publisher_name",
    "publisher_type",
    "posted_at",
)


@dataclass
class RunStats:
    found: int = 0
    new: int = 0
    rechecked: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)
    blocked: set[str] = field(default_factory=set)


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        sessions: sessionmaker,
        client: PoliteClient,
        adapters: dict[str, type[Adapter]] | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.client = client
        self.adapters = adapters or ADAPTERS

    # ------------------------------------------------------------------ rulare

    def run(self, profiles: list[Profile], pages: int) -> RunStats:
        stats = RunStats()
        with self.sessions() as session:
            if linked := link_missing_publishers(session):
                log.info("%d anunțuri legate de publicatori", linked)
            session.commit()
        for profile in profiles:
            for platform, source in profile.sources.items():
                if not source.enabled:
                    continue
                adapter_cls = self.adapters.get(platform)
                if adapter_cls is None:
                    log.warning("profil %s: platformă necunoscută %s", profile.name, platform)
                    continue
                self._run_source(profile, adapter_cls(self.client, self.settings), source.url, pages, stats)
        self._recheck(profiles, stats)
        self._osm_lookups(stats)
        return stats

    def _osm_lookups(self, stats: RunStats) -> None:
        """Anul construcției din OpenStreetMap pentru câteva locații noi (ADR 0005)."""
        with self.sessions() as session:
            for latitude, longitude in pending_locations(session, self.settings.osm_lookups_per_run):
                try:
                    row = osm_lookup(session, self.client, self.settings.overpass_url, latitude, longitude)
                    session.commit()
                    log.info("OpenStreetMap %s,%s: %s", latitude, longitude, row.start_date or "fără an")
                except BlockedError as exc:
                    session.rollback()
                    log.warning("Overpass refuză cererile, reîncerc la rularea următoare: %s", exc)
                    return
                except Exception as exc:  # o eroare de rețea nu oprește rularea
                    session.rollback()
                    log.warning("interogarea OpenStreetMap a eșuat (%s,%s): %s", latitude, longitude, exc)
                    stats.errors.append(f"osm: {exc}")
                    return

    def _run_source(self, profile: Profile, adapter: Adapter, url: str, pages: int, stats: RunStats) -> None:
        # Valorile implicite ale coloanelor se aplică abia la salvare, deci contoarele pornesc explicit de la 0.
        run = RunLog(profile=profile.name, platform=adapter.name, pages=pages, found=0, new=0)
        with self.sessions() as session:
            try:
                for item in adapter.search(url, pages):
                    run.found += 1
                    is_new = self._handle(session, profile, adapter, item, stats)
                    run.new += int(is_new)
                    session.commit()
            except BlockedError as exc:
                session.rollback()  # anunțul la care s-a oprit rămâne pentru rularea următoare
                run.error = str(exc)
                stats.blocked.add(adapter.name)
                log.warning("%s blocat: %s", adapter.name, exc)
            except Exception as exc:  # un adapter stricat nu oprește celelalte
                session.rollback()
                run.error = f"{type(exc).__name__}: {exc}"
                log.exception("%s a eșuat", adapter.name)
            session.add(run)
            session.commit()
            stats.found += run.found
            stats.new += run.new
            # Problemele rămân în run_log; interfața le arată pe pagina de stare (/status).
            if run.error:
                stats.errors.append(f"{adapter.name}: {run.error}")
            elif run.found == 0:
                log.warning(
                    "%s nu a întors niciun anunț: probabil s-a schimbat structura paginii sau URL-ul de căutare",
                    PLATFORM_NAMES.get(adapter.name, adapter.name),
                )

    # --------------------------------------------------------------- un anunț

    def _handle(self, session: Session, profile: Profile, adapter: Adapter, item: ListingData, stats: RunStats) -> bool:
        listing = session.scalar(
            select(Listing).where(Listing.platform == item.platform, Listing.external_id == item.external_id)
        )
        is_new = listing is None
        if is_new:
            result = match(profile, item)
            if self._worth_detail(item, result):
                item = self._detail(adapter, item)
                result = match(profile, item)
            listing = Listing(platform=item.platform, external_id=item.external_id)
            self._copy(listing, item)
            listing.detail_fetched = item.is_detail
            session.add(listing)
            session.flush()
            session.add(ListingSnapshot(listing=listing, price_eur=listing.price_eur))
            photo_urls = item.photo_urls
        else:
            self._refresh(session, listing, item)
            if listing.property_id is not None:
                return False  # deja grupat: interfața îl evaluează pentru fiecare profil
            # Anunț cunoscut care nu trecuse de profil: poate a scăzut prețul în interval, sau profilul
            # s-a schimbat. Îl reevaluăm cu datele salvate.
            data = listing_to_data(listing)
            data.photo_urls = item.photo_urls
            result = match(profile, data)
            if self._worth_detail(data, result):
                data = self._detail(adapter, data)
                self._copy(listing, data)
                listing.detail_fetched = data.is_detail
                result = match(profile, data)
            photo_urls = data.photo_urls

        link_publisher(session, listing)
        if result.passed:
            if not listing.photos:
                self._download_photos(session, listing, photo_urls)
            assign_property(session, listing)
            log.info("potrivire %s: %s (%s)", profile.name, listing.url, result.score)
        return is_new

    @staticmethod
    def _detail(adapter: Adapter, item: ListingData) -> ListingData:
        try:
            return adapter.fetch_detail(item)
        except BlockedError:
            raise
        except Exception as exc:
            log.warning("detaliu eșuat pentru %s: %s", item.url, exc)
            return item

    @staticmethod
    def _worth_detail(item: ListingData, result: MatchResult) -> bool:
        if item.is_detail:
            return False
        if result.passed:
            return True
        # Fără preț în pagina de căutare (de ex. Imobiliare.ro): doar detaliul poate decide.
        return item.price_eur is None

    def _copy(self, listing: Listing, item: ListingData) -> None:
        for name in _COPIED_FIELDS:
            setattr(listing, name, getattr(item, name))
        listing.extra = dict(item.extra)
        hashed = phone_hash(item.phone)
        if hashed:
            listing.phone_hash = hashed

    @staticmethod
    def _refresh(session: Session, listing: Listing, item: ListingData) -> None:
        """Anunțul e încă activ: actualizează prețul și păstrează un snapshot dacă s-a schimbat."""
        listing.last_seen = utcnow()
        listing.active = True
        listing.removed_at = None
        old_price = listing.price_eur
        new_price = item.price_eur
        if new_price is None or old_price is None or abs(new_price - old_price) < 1:
            return
        listing.price, listing.currency, listing.price_eur = item.price, item.currency, new_price
        session.add(ListingSnapshot(listing=listing, price_eur=new_price))
        log.info("preț schimbat %s: %s → %s", listing.url, old_price, new_price)

    # ----------------------------------------------------------- reverificare

    def _recheck(self, profiles: list[Profile], stats: RunStats) -> None:
        """Redeschide pagina de detaliu a anunțurilor potrivite care nu au mai apărut în căutare.

        Căutarea confirmă deja anunțurile pe care le listează, așa că reverificăm doar ce a ieșit din
        primele pagini: poate s-a schimbat prețul, poate a dispărut (vândut sau retras).
        """
        if self.settings.recheck_per_run <= 0:
            return
        platforms = {
            platform
            for profile in profiles
            for platform, source in profile.sources.items()
            if source.enabled and platform in self.adapters
        } - stats.blocked
        if not platforms:
            return
        cutoff = utcnow() - timedelta(hours=self.settings.recheck_after_hours)
        adapters: dict[str, Adapter] = {}
        with self.sessions() as session:
            due = session.scalars(
                select(Listing)
                .where(
                    Listing.active.is_(True),
                    Listing.property_id.is_not(None),  # doar anunțurile care au trecut de un profil
                    Listing.platform.in_(platforms),
                    Listing.last_seen < cutoff,
                    or_(Listing.checked_at.is_(None), Listing.checked_at < cutoff),
                )
                .order_by(Listing.checked_at.is_not(None), Listing.checked_at, Listing.last_seen)
                .limit(self.settings.recheck_per_run)
            ).all()
            for listing in due:
                if listing.platform in stats.blocked:
                    continue
                adapter = adapters.setdefault(
                    listing.platform, self.adapters[listing.platform](self.client, self.settings)
                )
                self._recheck_one(session, adapter, listing, stats)
                session.commit()
        if due:
            log.info("reverificare: %d anunțuri, %d dispărute", stats.rechecked, stats.removed)

    def _recheck_one(self, session: Session, adapter: Adapter, listing: Listing, stats: RunStats) -> None:
        now = utcnow()
        try:
            item = adapter.fetch_detail(listing_to_data(listing))
        except BlockedError as exc:
            stats.blocked.add(listing.platform)
            log.warning("reverificare oprită pe %s: %s", listing.platform, exc)
            return
        except ListingRemoved:
            self._mark_removed(listing, now, stats)
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 410):
                self._mark_removed(listing, now, stats)
            else:
                listing.checked_at = now
                log.warning("reverificare eșuată %s: %s", listing.url, exc)
            return
        except Exception as exc:  # pagina s-a schimbat sau rețeaua a căzut: reîncercăm mâine
            listing.checked_at = now
            log.warning("reverificare eșuată %s: %s", listing.url, exc)
            return
        listing.checked_at = now
        stats.rechecked += 1
        self._refresh(session, listing, item)

    @staticmethod
    def _mark_removed(listing: Listing, now: datetime, stats: RunStats) -> None:
        listing.active = False
        listing.removed_at = now
        listing.checked_at = now
        stats.rechecked += 1
        stats.removed += 1
        log.info("anunț dispărut: %s", listing.url)

    def _download_photos(self, session: Session, listing: Listing, urls: list[str]) -> None:
        for position, url in enumerate(urls[: self.settings.photos_per_listing]):
            photo = Photo(listing=listing, position=position, source_url=url)
            try:
                data = self.client.get_bytes(url)
                hashes = image_hashes(data)
                if hashes:
                    photo.phash, photo.dhash = hashes
                    photo.path = str(
                        save_image(self.settings.photos_dir, listing.platform, listing.external_id, position, data)
                    )
            except Exception as exc:
                log.debug("poză eșuată %s: %s", url, exc)
            session.add(photo)
        session.flush()


def listing_to_data(listing: Listing) -> ListingData:
    data = ListingData(
        platform=listing.platform, external_id=listing.external_id, url=listing.url, is_detail=listing.detail_fetched
    )
    for name in _COPIED_FIELDS:
        setattr(data, name, getattr(listing, name))
    return data
