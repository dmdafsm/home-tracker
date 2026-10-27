"""O rulare: caută pe fiecare platformă, salvează, grupează pe apartamente, notifică."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .adapters import ADAPTERS, Adapter
from .config import Settings
from .dedup import assign_property
from .http import BlockedError, PoliteClient
from .models import Alert, Listing, ListingSnapshot, Photo, RunLog, utcnow
from .notify import PLATFORM_NAMES, Message, Notifier, floor_label, format_price
from .parsing import phone_hash
from .photos import image_hashes, save_image
from .profiles import MatchResult, Profile, match
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
WARNING_COOLDOWN = timedelta(hours=12)


@dataclass
class RunStats:
    found: int = 0
    new: int = 0
    alerts: int = 0
    errors: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        sessions: sessionmaker,
        client: PoliteClient,
        notifier: Notifier,
        adapters: dict[str, type[Adapter]] | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.client = client
        self.notifier = notifier
        self.adapters = adapters or ADAPTERS

    # ------------------------------------------------------------------ rulare

    def run(self, profiles: list[Profile], pages: int) -> RunStats:
        stats = RunStats()
        for profile in profiles:
            for platform, source in profile.sources.items():
                if not source.enabled:
                    continue
                adapter_cls = self.adapters.get(platform)
                if adapter_cls is None:
                    log.warning("profil %s: platformă necunoscută %s", profile.name, platform)
                    continue
                self._run_source(profile, adapter_cls(self.client, self.settings), source.url, pages, stats)
        return stats

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
                log.warning("%s blocat: %s", adapter.name, exc)
            except Exception as exc:  # un adapter stricat nu oprește celelalte
                session.rollback()
                run.error = f"{type(exc).__name__}: {exc}"
                log.exception("%s a eșuat", adapter.name)
            session.add(run)
            session.commit()
            stats.found += run.found
            stats.new += run.new
            if run.error:
                stats.errors.append(f"{adapter.name}: {run.error}")
                self._warn(
                    session, profile, adapter.name, f"{PLATFORM_NAMES.get(adapter.name, adapter.name)}: {run.error}"
                )
            elif run.found == 0:
                self._warn(
                    session,
                    profile,
                    adapter.name,
                    f"{PLATFORM_NAMES.get(adapter.name, adapter.name)} nu a întors niciun anunț. "
                    "Probabil s-a schimbat structura paginii sau URL-ul de căutare.",
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
            self._update_existing(session, profile, listing, item, stats)
            if listing.property_id is not None and self._already_alerted(session, profile, listing.property_id, "new"):
                return False
            # Anunț cunoscut, încă neanunțat pentru acest profil: poate a scăzut prețul în interval,
            # sau profilul e nou. Îl reevaluăm cu datele salvate.
            data = _as_data(listing)
            data.photo_urls = item.photo_urls
            result = match(profile, data)
            if self._worth_detail(data, result):
                data = self._detail(adapter, data)
                self._copy(listing, data)
                listing.detail_fetched = data.is_detail
                result = match(profile, data)
            photo_urls = data.photo_urls

        if result.passed:
            if not listing.photos:
                self._download_photos(session, listing, photo_urls)
            prop = assign_property(session, listing)
            log.info("potrivire %s: %s (%s)", profile.name, listing.url, result.score)
            if result.score >= profile.notify.min_score and not self._already_alerted(session, profile, prop.id, "new"):
                self._alert_new(session, profile, listing, result)
                stats.alerts += 1
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

    def _update_existing(
        self, session: Session, profile: Profile, listing: Listing, item: ListingData, stats: RunStats
    ) -> None:
        listing.last_seen = utcnow()
        listing.active = True
        old_price = listing.price_eur
        new_price = item.price_eur
        if new_price is None or old_price is None or abs(new_price - old_price) < 1:
            return
        listing.price, listing.currency, listing.price_eur = item.price, item.currency, new_price
        session.add(ListingSnapshot(listing=listing, price_eur=new_price))
        log.info("preț schimbat %s: %s → %s", listing.url, old_price, new_price)

        drop = (old_price - new_price) / old_price * 100
        if drop < profile.notify.price_drop_percent or listing.property_id is None:
            return
        if not self._already_alerted(session, profile, listing.property_id, "new"):
            return  # anunțul nu a fost niciodată relevant pentru acest profil
        result = match(profile, _as_data(listing))
        if not result.passed:
            return
        last_drop = session.scalar(
            select(func.min(Alert.price_eur)).where(
                Alert.profile == profile.name, Alert.property_id == listing.property_id, Alert.kind == "price_drop"
            )
        )
        if last_drop is not None and new_price >= last_drop:
            return
        self._send(
            session,
            profile,
            listing,
            result,
            "price_drop",
            title=f"Preț scăzut cu {drop:.0f}% · {format_price(new_price)}",
            lead=f"Înainte: {format_price(old_price)}",
        )
        stats.alerts += 1

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

    # ---------------------------------------------------------------- alerte

    @staticmethod
    def _already_alerted(session: Session, profile: Profile, property_id: int, kind: str) -> bool:
        return (
            session.scalar(
                select(func.count(Alert.id)).where(
                    Alert.profile == profile.name, Alert.property_id == property_id, Alert.kind == kind
                )
            )
            > 0
        )

    def _alert_new(self, session: Session, profile: Profile, listing: Listing, result: MatchResult) -> None:
        parts = [format_price(listing.price_eur), floor_label(listing.floor)]
        if listing.area_m2:
            parts.append(f"{listing.area_m2:g} mp")
        if listing.rooms:
            parts.append(f"{listing.rooms} cam.")
        self._send(session, profile, listing, result, "new", title="Apartament nou · " + " · ".join(parts))

    def _send(
        self,
        session: Session,
        profile: Profile,
        listing: Listing,
        result: MatchResult,
        kind: str,
        *,
        title: str,
        lead: str = "",
    ) -> None:
        publisher = {"owner": "proprietar", "agency": "agenție"}.get(
            listing.publisher_type or "", "publicator necunoscut"
        )
        if listing.publisher_name:
            publisher += f" ({listing.publisher_name})"
        others = session.scalars(
            select(Listing).where(Listing.property_id == listing.property_id, Listing.id != listing.id)
        ).all()
        lines = [
            line
            for line in (
                lead,
                listing.title,
                f"{PLATFORM_NAMES.get(listing.platform, listing.platform)} · {publisher}",
                f"Scor {result.score:.0f}: " + ", ".join(result.reasons)
                if result.reasons
                else f"Scor {result.score:.0f}",
            )
            if line
        ]
        if others:
            lines.append(
                "Apare și pe: "
                + ", ".join(f"{PLATFORM_NAMES.get(o.platform, o.platform)} {format_price(o.price_eur)}" for o in others)
            )
        first_photo = next((p.source_url for p in listing.photos if p.phash), None)
        self.notifier.send(
            Message(
                title=title,
                body="\n".join(lines),
                url=listing.url,
                image_url=first_photo,
                tags=["house"] if kind == "new" else ["chart_with_downwards_trend"],
                priority=4 if kind == "new" else 3,
            ),
            profile.notify.channels,
        )
        session.add(
            Alert(
                profile=profile.name,
                property_id=listing.property_id,
                listing_id=listing.id,
                kind=kind,
                score=result.score,
                price_eur=listing.price_eur,
            )
        )
        session.flush()

    def _warn(self, session: Session, profile: Profile, platform: str, text: str) -> None:
        """Avertismente tehnice, cel mult unul la 12 ore pe platformă."""
        since = datetime.now(UTC) - WARNING_COOLDOWN
        recent = session.scalars(
            select(RunLog)
            .where(RunLog.platform == platform, RunLog.started_at >= since)
            .order_by(RunLog.started_at.desc())
        ).all()
        problems = [r for r in recent if r.error or r.found == 0]
        if len(problems) > 1:  # rularea curentă e deja în listă; o problemă anterioară = am anunțat deja
            return
        self.notifier.send(
            Message(title="home-tracker: problemă la colectare", body=text, tags=["warning"], priority=2),
            profile.notify.channels,
        )


def _as_data(listing: Listing) -> ListingData:
    data = ListingData(
        platform=listing.platform, external_id=listing.external_id, url=listing.url, is_detail=listing.detail_fetched
    )
    for name in _COPIED_FIELDS:
        setattr(data, name, getattr(listing, name))
    return data
