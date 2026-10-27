"""Interfața web: feed-ul apartamentelor potrivite, pagina unui apartament, etichetele și editorul de profiluri."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy.orm import sessionmaker

from . import profile_editor
from .authenticity import Context, assess
from .buildyear import estimate_for
from .config import Settings
from .feed import PAGE_PHOTOS, PUBLISHERS, ROOMS, SORTS, SUGGESTION_PHOTOS, FeedFilters, feed, photo_ids
from .formatting import PLATFORM_NAMES, floor_label, format_price
from .history import PropertyHistory, match_label, property_history, publisher_label
from .labels import ACTIONS, apply_label, label_for, split_listing
from .merge import mark_distinct, merge_properties, possible_duplicates
from .models import Photo, as_utc, init_db, make_engine, utcnow
from .photos import image_path
from .profiles import Profile, load_profiles
from .publishers import DISGUISED_AGENT_PROPERTIES, publisher_stats
from .status import collection_status

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def safe_url(url: str | None) -> str | None:
    """Doar legături http(s): URL-ul vine din pagina platformei, iar escaparea nu oprește `javascript:`."""
    return url if url and urlsplit(url).scheme in ("http", "https") else None


def back_to(target: str | None, default: str) -> str:
    """Pagina la care se întoarce un buton: doar o cale din aplicație, niciodată alt site."""
    if target and target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return default


_BULLET = re.compile(r"^\s*(?:[-–—•*·▪►✓✔☑➤]|\d{1,2}[.)])\s+")


def paragraphs(text: str | None) -> Markup:
    """Textul unui anunț ca HTML citibil: paragrafe la rânduri goale, liste din rândurile cu „-”, „•”, „1.”."""
    html = []
    for block in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip()):
        prose: list[str] = []
        items: list[str] = []
        for line in [line.strip() for line in block.split("\n") if line.strip()] + [None]:
            bullet = line is not None and _BULLET.match(line)
            if prose and (line is None or bullet):
                html.append("<p>" + "<br>".join(str(escape(part)) for part in prose) + "</p>")
                prose = []
            if items and (line is None or not bullet):
                html.append("<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>")
                items = []
            if bullet:
                items.append(_BULLET.sub("", line))
            elif line is not None:
                prose.append(line)
    return Markup("".join(html))


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

    def moment(value: datetime | None) -> str:
        return as_utc(value).astimezone(zone).strftime("%d.%m.%Y %H:%M") if value else "–"

    TEMPLATES.env.globals.update(
        price=format_price,
        floor=floor_label,
        day=day,
        moment=moment,
        platform=lambda name: PLATFORM_NAMES.get(name, name),
        publisher=publisher_label,
        match_label=match_label,
        safe_url=safe_url,
        plural=plural,
        area=area_label,
        paragraphs=paragraphs,
    )

    def profiles() -> list[Profile]:
        return load_profiles(settings.profiles_dir)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, profile: str | None = None):
        available = profiles()
        if not available:
            raise HTTPException(404, "niciun profil activ")
        selected = next((p for p in available if p.name == profile), available[0])
        filters = FeedFilters.from_query(request.query_params, request.query_params.getlist)
        with sessions() as session:
            items = feed(session, selected, filters)
            return TEMPLATES.TemplateResponse(
                request,
                "feed.html",
                {
                    "profiles": available,
                    "profile": selected,
                    "items": items,
                    "filters": filters,
                    "here": request.url.path + ("?" + request.url.query if request.url.query else ""),
                    "sorts": SORTS,
                    "room_options": ROOMS,
                    "publisher_options": PUBLISHERS,
                    "platform_options": sorted(
                        {name for p in available for name, source in p.sources.items() if source.enabled}
                    ),
                    "problems": [s for s in collection_status(session, available, settings) if s.problem],
                    "now": utcnow(),
                },
            )

    @app.get("/status", response_class=HTMLResponse)
    def status_page(request: Request):
        with sessions() as session:
            return TEMPLATES.TemplateResponse(
                request, "status.html", {"platforms": collection_status(session, profiles(), settings)}
            )

    @app.get("/property/{property_id}", response_class=HTMLResponse)
    def property_page(request: Request, property_id: int):
        with sessions() as session:
            history = property_history(session, property_id)
            if history is None:
                raise HTTPException(404, "apartament inexistent")
            duplicates = possible_duplicates(session, [property_id]).get(property_id, [])
            return TEMPLATES.TemplateResponse(
                request,
                "property.html",
                {
                    "history": history,
                    "photos": photo_ids(session, property_id, PAGE_PHOTOS),
                    "duplicate_photos": {
                        d.property_id: photo_ids(session, d.property_id, SUGGESTION_PHOTOS) for d in duplicates
                    },
                    "chart": price_chart(history),
                    "assessment": assess(history, Context.build(session)),
                    "label": label_for(session, property_id),
                    "year": estimate_for(session, history.listings),
                    "duplicates": duplicates,
                    "now": utcnow(),
                },
            )

    def require_same_origin(request: Request) -> None:
        # Formularele trimit Origin (sau măcar Referer); un POST de pe alt site e refuzat (CSRF).
        origin = request.headers.get("origin")
        source = origin if origin and origin != "null" else request.headers.get("referer")
        if not source or urlsplit(source).netloc != request.headers.get("host"):
            raise HTTPException(403, "cerere din afara aplicației")

    @app.post("/property/{property_id}/label/{action}")
    def label_property(request: Request, property_id: int, action: str, next: str | None = None):
        require_same_origin(request)
        if action not in ACTIONS:
            raise HTTPException(400, "acțiune necunoscută")
        with sessions() as session:
            try:
                apply_label(session, property_id, action)
            except LookupError:
                raise HTTPException(404, "apartament inexistent") from None
            session.commit()
        return RedirectResponse(back_to(next, f"/property/{property_id}"), status_code=303)

    @app.post("/listing/{listing_id}/split")
    def split(request: Request, listing_id: int):
        require_same_origin(request)
        with sessions() as session:
            try:
                new_property = split_listing(session, listing_id)
            except LookupError:
                raise HTTPException(404, "anunț inexistent") from None
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            session.commit()
        return RedirectResponse(f"/property/{new_property}", status_code=303)

    @app.post("/property/{property_id}/merge/{other_id}")
    def merge(request: Request, property_id: int, other_id: int):
        require_same_origin(request)
        with sessions() as session:
            try:
                kept = merge_properties(session, property_id, other_id)
            except LookupError:
                raise HTTPException(404, "apartament inexistent") from None
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            session.commit()
        return RedirectResponse(f"/property/{kept}", status_code=303)

    @app.post("/property/{property_id}/distinct/{other_id}")
    def distinct(request: Request, property_id: int, other_id: int):
        require_same_origin(request)
        with sessions() as session:
            try:
                mark_distinct(session, property_id, other_id)
            except LookupError:
                raise HTTPException(404, "apartament inexistent") from None
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            session.commit()
        return RedirectResponse(f"/property/{property_id}", status_code=303)

    @app.get("/publishers", response_class=HTMLResponse)
    def publishers_page(request: Request):
        with sessions() as session:
            return TEMPLATES.TemplateResponse(
                request,
                "publishers.html",
                {"stats": publisher_stats(session), "agent_threshold": DISGUISED_AGENT_PROPERTIES},
            )

    @app.get("/publisher/{publisher_id}", response_class=HTMLResponse)
    def publisher_page(request: Request, publisher_id: int):
        with sessions() as session:
            found = publisher_stats(session, publisher_id)
            if not found:
                raise HTTPException(404, "publicator inexistent")
            stats = found[0]
            listings = sorted(stats.listings, key=lambda item: as_utc(item.first_seen), reverse=True)
            taken = {listing.id for listing in stats.taken_over}
            return TEMPLATES.TemplateResponse(
                request,
                "publisher.html",
                {"stats": stats, "listings": listings, "taken": taken, "agent_threshold": DISGUISED_AGENT_PROPERTIES},
            )

    async def form(request: Request) -> dict[str, str]:
        # Formular urlencoded citit direct, fără python-multipart; profilurile sunt mici.
        if not request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            raise HTTPException(415, "formular neașteptat")
        body = await request.body()
        if len(body) > 4 * profile_editor.MAX_BYTES:
            raise HTTPException(413, "formular prea mare")
        return {key: values[0] for key, values in parse_qs(body.decode(), keep_blank_values=True).items()}

    def edit_page(request: Request, *, text: str, original, error=None, preview=None, status_code=200):
        return TEMPLATES.TemplateResponse(
            request,
            "profile_edit.html",
            {"text": text, "original": original, "error": error, "preview": preview},
            status_code=status_code,
        )

    def preview_for(profile: Profile, original) -> dict:
        with sessions() as session:
            after = len(feed(session, profile))
            before = len(feed(session, original.profile)) if original and original.profile else None
        return {"profile": profile, "after": after, "before": before}

    @app.get("/profiles", response_class=HTMLResponse)
    def profiles_page(request: Request, saved: str | None = None):
        files = profile_editor.read_files(settings.profiles_dir)
        return TEMPLATES.TemplateResponse(request, "profiles.html", {"files": files, "saved": saved})

    @app.get("/profiles/new", response_class=HTMLResponse)
    def new_profile(request: Request, source: str | None = None):
        original = profile_editor.find(settings.profiles_dir, source) if source else None
        if source and original is None:
            raise HTTPException(404, "profil inexistent")
        text = profile_editor.copy_text(original, f"{original.name}-copie") if original else "name: profil-nou\n"
        return edit_page(request, text=text, original=None)

    @app.get("/profiles/{name}", response_class=HTMLResponse)
    def edit_profile(request: Request, name: str):
        original = profile_editor.find(settings.profiles_dir, name)
        if original is None:
            raise HTTPException(404, "profil inexistent")
        return edit_page(request, text=original.text, original=original)

    async def submit(request: Request, original):
        require_same_origin(request)
        fields = await form(request)
        text = fields.get("text", "")
        try:
            if fields.get("action") == "save":
                saved = profile_editor.save(settings.profiles_dir, text, original=original)
                return RedirectResponse(f"/profiles?saved={saved.name}", status_code=303)
            preview = preview_for(profile_editor.parse(text), original)
        except profile_editor.ProfileError as exc:
            return edit_page(request, text=text, original=original, error=str(exc), status_code=422)
        return edit_page(request, text=text, original=original, preview=preview)

    @app.post("/profiles/new", response_class=HTMLResponse)
    async def create_profile(request: Request):
        return await submit(request, None)

    @app.post("/profiles/{name}", response_class=HTMLResponse)
    async def update_profile(request: Request, name: str):
        original = profile_editor.find(settings.profiles_dir, name)
        if original is None:
            raise HTTPException(404, "profil inexistent")
        return await submit(request, original)

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
