"""Interfața web (doar citire): feed-ul apartamentelor potrivite și pagina unui apartament."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .config import Settings
from .history import PropertyHistory, match_label, property_history, publisher_label
from .models import Alert, Listing, Photo, as_utc, init_db, make_engine, utcnow
from .notify import PLATFORM_NAMES, floor_label, format_price
from .photos import image_path
from .pipeline import listing_to_data
from .profiles import MatchResult, Profile, load_profiles, match

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def safe_url(url: str | None) -> str | None:
    """Doar legături http(s): URL-ul vine din pagina platformei, iar escaparea nu oprește `javascript:`."""
    return url if url and urlsplit(url).scheme in ("http", "https") else None


@dataclass
class FeedItem:
    history: PropertyHistory
    listing: Listing  # anunțul cu scorul cel mai bun pentru profil
    result: MatchResult
    photo_id: int | None
    alerted: bool

    @property
    def platforms(self) -> list[str]:
        return sorted({PLATFORM_NAMES.get(item.platform, item.platform) for item in self.history.listings})


def feed(session, profile: Profile, *, include_removed: bool = False) -> list[FeedItem]:
    """Apartamentele cu cel puțin un anunț care trece de filtrele profilului, după scor."""
    property_ids = session.scalars(select(Listing.property_id).where(Listing.property_id.is_not(None)).distinct())
    alerted = set(
        session.scalars(select(Alert.property_id).where(Alert.profile == profile.name, Alert.kind == "new")).all()
    )
    items = []
    for property_id in property_ids.all():
        history = property_history(session, property_id)
        if history is None or (not history.active and not include_removed):
            continue
        candidates = history.active or history.listings
        scored = [(listing, match(profile, listing_to_data(listing))) for listing in candidates]
        scored = [(listing, result) for listing, result in scored if result.passed]
        if not scored:
            continue
        listing, result = max(scored, key=lambda pair: pair[1].score)
        items.append(FeedItem(history, listing, result, _first_photo_id(session, history), property_id in alerted))
    items.sort(key=lambda item: (-item.result.score, -as_utc(item.history.listings[-1].first_seen).timestamp()))
    return items


def _first_photo_id(session, history: PropertyHistory) -> int | None:
    return session.scalar(
        select(Photo.id)
        .where(Photo.listing_id.in_([listing.id for listing in history.listings]), Photo.path.is_not(None))
        .order_by(Photo.listing_id, Photo.position)
        .limit(1)
    )


def plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def area_label(value: float | None) -> str:
    return f"{value:g} mp" if value else ""


def price_chart(history: PropertyHistory, width: int = 640, height: int = 140) -> Markup:
    """Graficul prețului ca SVG (o linie pe anunț), cu etichetele și legenda în HTML, lizibile pe telefon."""
    points = history.prices
    if len(points) < 2:
        return Markup("")
    times = [as_utc(p.seen_at).timestamp() for p in points]
    prices = [p.price_eur for p in points]
    t0, t1 = min(times), max(times)
    low, high = min(prices), max(prices)
    pad_x, pad_y = 8, 16
    span_t = (t1 - t0) or 1
    span_p = (high - low) or max(high * 0.02, 1)

    def x(t: float) -> float:
        return pad_x + (t - t0) / span_t * (width - 2 * pad_x)

    def y(price: float) -> float:
        return height - pad_y - (price - low) / span_p * (height - 2 * pad_y)

    lines = []
    by_listing: dict[int, list[tuple[float, float]]] = {}
    for t, point in zip(times, points, strict=True):
        by_listing.setdefault(point.listing.id, []).append((x(t), y(point.price_eur)))
    for index, (listing_id, coords) in enumerate(by_listing.items()):
        if len(coords) == 1:
            coords = [coords[0], (coords[0][0] + 0.01, coords[0][1])]
        path = " ".join(f"{cx:.1f},{cy:.1f}" for cx, cy in coords)
        lines.append(
            f'<polyline class="series s{index % 4}" points="{path}" fill="none" stroke-width="2">'
            f"<title>anunțul {listing_id}</title></polyline>"
        )
        cx, cy = coords[-1]
        lines.append(f'<circle class="series s{index % 4}" cx="{cx:.1f}" cy="{cy:.1f}" r="3"/>')
    platforms = {point.listing.id: point.listing.platform for point in points}
    legend = "".join(
        f'<span class="key s{index % 4}">{escape(PLATFORM_NAMES.get(platforms[listing_id], platforms[listing_id]))} '
        f"#{listing_id}</span>"
        for index, listing_id in enumerate(by_listing)
    )
    return Markup(
        f'<div class="axis small muted">{escape(format_price(high))}</div>'
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Istoricul prețului" class="chart">'
        f"{''.join(lines)}</svg>"
        f'<div class="axis small muted">{escape(format_price(low))}</div>'
        f'<div class="legend small">{legend}</div>'
    )


def create_app(settings: Settings, sessions: sessionmaker | None = None) -> FastAPI:
    sessions = sessions or init_db(make_engine(settings.database_url))
    zone = ZoneInfo(settings.timezone)
    photos_root = settings.photos_dir.resolve()
    app = FastAPI(title="home-tracker", docs_url=None, redoc_url=None, openapi_url=None)

    def day(value: datetime | None) -> str:
        return as_utc(value).astimezone(zone).strftime("%d.%m.%Y") if value else "–"

    TEMPLATES.env.globals.update(
        price=format_price,
        floor=floor_label,
        day=day,
        platform=lambda name: PLATFORM_NAMES.get(name, name),
        publisher=publisher_label,
        match_label=match_label,
        safe_url=safe_url,
        plural=plural,
        area=area_label,
    )

    def profiles() -> list[Profile]:
        return load_profiles(settings.profiles_dir)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, profile: str | None = None, removed: bool = False):
        available = profiles()
        if not available:
            raise HTTPException(404, "niciun profil activ")
        selected = next((p for p in available if p.name == profile), available[0])
        with sessions() as session:
            items = feed(session, selected, include_removed=removed)
            return TEMPLATES.TemplateResponse(
                request,
                "feed.html",
                {"profiles": available, "profile": selected, "items": items, "removed": removed, "now": utcnow()},
            )

    @app.get("/property/{property_id}", response_class=HTMLResponse)
    def property_page(request: Request, property_id: int):
        with sessions() as session:
            history = property_history(session, property_id)
            if history is None:
                raise HTTPException(404, "apartament inexistent")
            photos = session.scalars(
                select(Photo)
                .where(Photo.listing_id.in_([listing.id for listing in history.listings]), Photo.path.is_not(None))
                .order_by(Photo.listing_id, Photo.position)
            ).all()
            return TEMPLATES.TemplateResponse(
                request,
                "property.html",
                {"history": history, "photos": photos, "chart": price_chart(history), "now": utcnow()},
            )

    @app.get("/photo/{photo_id}")
    def photo(photo_id: int):
        with sessions() as session:
            row = session.get(Photo, photo_id)
            if row is None or not row.path:
                raise HTTPException(404)
            # Calea se calculează din anunț, nu din `photo.path`: acolo e calea văzută de procesul care a
            # salvat poza (worker-ul are alt DATA_DIR decât o rulare pe host) și nu o folosim ca atare.
            try:
                path = image_path(photos_root, row.listing.platform, row.listing.external_id, row.position)
            except ValueError:
                raise HTTPException(404) from None
        path = path.resolve()
        if not path.is_relative_to(photos_root) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    return app
