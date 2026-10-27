"""Linia de comandă: `hometracker loop | run | summary | property | web | demo | dump | test-notify | matches`."""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import select

from .adapters import ADAPTERS
from .config import Settings, load_settings
from .history import find_property_id, format_history, property_history
from .http import PoliteClient
from .models import Alert, Listing, init_db, make_engine, utcnow
from .notify import Message, Notifier, floor_label, format_price
from .pipeline import Pipeline
from .profiles import load_profiles
from .summary import build_summary, due_period, format_summary, send_due_summaries, send_summary

log = logging.getLogger("hometracker")

HOST_TO_PLATFORM = {
    "storia.ro": "storia",
    "olx.ro": "olx",
    "imobiliare.ro": "imobiliare",
    "publi24.ro": "publi24",
    "lajumate.ro": "lajumate",
}


def _setup(settings: Settings):
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return init_db(make_engine(settings.database_url))


def _run_once(settings: Settings, sessions, *, catchup: bool, profile_name: str | None) -> None:
    profiles = load_profiles(settings.profiles_dir)
    if profile_name:
        profiles = [p for p in profiles if p.name == profile_name]
    if not profiles:
        log.error("niciun profil activ în %s", settings.profiles_dir)
        return
    pages = settings.pages_catchup if catchup else settings.pages_normal
    with PoliteClient(settings) as client:
        stats = Pipeline(settings, sessions, client, Notifier(settings)).run(profiles, pages)
    log.info(
        "rulare %s gata: %d văzute, %d noi, %d alerte, %d reverificate (%d dispărute)%s",
        "de recuperare" if catchup else "normală",
        stats.found,
        stats.new,
        stats.alerts,
        stats.rechecked,
        stats.removed,
        f", erori: {'; '.join(stats.errors)}" if stats.errors else "",
    )


def cmd_loop(args: argparse.Namespace) -> None:
    """Rulează la nesfârșit. După o pauză lungă (Mac închis), face o rulare de recuperare."""
    settings = load_settings()
    sessions = _setup(settings)
    max_gap = settings.interval_max_minutes * 60 * 2
    last_run: float | None = None
    while True:
        now = time.time()
        catchup = last_run is None or now - last_run > max_gap
        try:
            _run_once(settings, sessions, catchup=catchup, profile_name=None)
        except Exception:
            log.exception("rularea a eșuat; reîncerc la următorul interval")
        try:
            send_due_summaries(sessions, load_profiles(settings.profiles_dir), Notifier(settings), settings)
        except Exception:
            log.exception("rezumatul zilnic a eșuat; reîncerc la următorul interval")
        last_run = now
        wait = random.uniform(settings.interval_min_minutes, settings.interval_max_minutes) * 60
        target = time.time() + wait
        log.info("următoarea rulare la %s", datetime.fromtimestamp(target).strftime("%H:%M"))
        # Pași scurți, după ceasul real: după un sleep al calculatorului nu așteptăm restul intervalului.
        while time.time() < target:
            time.sleep(min(30, max(1, target - time.time())))


def cmd_run(args: argparse.Namespace) -> None:
    settings = load_settings()
    sessions = _setup(settings)
    _run_once(settings, sessions, catchup=args.catchup, profile_name=args.profile)


def cmd_summary(args: argparse.Namespace) -> None:
    """Rezumatul de acum: perioada de la ultimul rezumat trimis (sau ultimele 24 de ore) până acum."""
    settings = load_settings()
    sessions = _setup(settings)
    profiles = [p for p in load_profiles(settings.profiles_dir) if not args.profile or p.name == args.profile]
    # Ora din SUMMARY_HOUR nu contează aici: comanda e pornită de mână.
    now_settings = replace(settings, summary_hour=0)
    for profile in profiles:
        with sessions() as session:
            period = due_period(session, profile, utcnow(), now_settings)
            if period is None or args.print:
                period = period or (utcnow() - timedelta(hours=24), utcnow())
            if args.print:
                message = format_summary(build_summary(session, profile, *period, settings), settings)
                print(f"{message.title}\n\n{message.body}\n")
                continue
        if period is None:
            print(f"{profile.name}: rezumatul de azi a fost deja trimis")
        elif not send_summary(sessions, profile, Notifier(settings), settings, *period):
            print(f"{profile.name}: rezumatul nu a putut fi trimis (canale configurate?)")


def cmd_property(args: argparse.Namespace) -> None:
    """Istoricul unui apartament: anunțurile, publicatorii, prețurile, republicările."""
    settings = load_settings()
    sessions = _setup(settings)
    with sessions() as session:
        property_id = find_property_id(session, args.reference)
        history = property_history(session, property_id) if property_id is not None else None
        if history is None:
            sys.exit(f"nu am găsit apartamentul {args.reference}")
        print(format_history(history, settings))


def cmd_web(args: argparse.Namespace) -> None:
    """Interfața web. Implicit doar pe localhost; în Docker ascultă pe 0.0.0.0, publicat doar pe 127.0.0.1."""
    import uvicorn

    from .web import create_app

    settings = load_settings()
    sessions = _setup(settings)
    uvicorn.run(create_app(settings, sessions), host=args.host, port=args.port, log_level="warning")


def cmd_demo(args: argparse.Namespace) -> None:
    """Date sintetice pentru interfață, într-un DATA_DIR separat (de exemplu data/demo)."""
    from .demo import create_demo

    settings = load_settings()
    _setup(settings)
    try:
        count = create_demo(settings, args.apartments)
    except RuntimeError as exc:
        sys.exit(str(exc))
    print(f"{count} anunțuri demo în {settings.database_url}")


def cmd_dump(args: argparse.Namespace) -> None:
    """Descarcă o pagină, o salvează în data/debug și arată ce extrage adapterul din ea."""
    settings = load_settings()
    _setup(settings)
    host = urlparse(args.url).netloc.removeprefix("www.")
    platform = HOST_TO_PLATFORM.get(host)
    if platform is None:
        sys.exit(f"nu știu ce adapter să folosesc pentru {host}")
    with PoliteClient(settings) as client:
        html = client.get(args.url, archive=False).text
        adapter = ADAPTERS[platform](client, settings)
    settings.debug_dir.mkdir(parents=True, exist_ok=True)
    path = settings.debug_dir / f"{platform}-{datetime.now(UTC):%Y%m%d-%H%M%S}.html"
    path.write_text(html, encoding="utf-8")
    print(f"pagina salvată în {path} ({len(html):,} caractere)")
    items = list(adapter.parse_search(html, args.url))
    if items:
        print(f"\nca pagină de căutare: {len(items)} anunțuri")
        for item in items[:10]:
            print(
                f"  {item.external_id:>14}  {format_price(item.price_eur):>14}  {floor_label(item.floor):<16} "
                f"{item.area_m2 or '?':>5} mp  {item.title[:60]}"
            )
    detail = adapter.parse_detail(html, args.url)
    if detail.title or detail.price_eur:
        print("\nca pagină de detaliu:")
        for name in (
            "title",
            "price_eur",
            "area_m2",
            "rooms",
            "floor",
            "balcony",
            "publisher_type",
            "publisher_name",
            "location_text",
        ):
            print(f"  {name:<15} {getattr(detail, name)!r}")
        print(f"  {'photos':<15} {len(detail.photo_urls)}")
        print(f"  {'description':<15} {detail.description[:200]!r}")
    if not items and not (detail.title or detail.price_eur):
        print("\nadapterul nu a găsit nimic: structura paginii trebuie verificată.")


def cmd_test_notify(args: argparse.Namespace) -> None:
    settings = load_settings()
    logging.basicConfig(level=logging.INFO)
    channels = args.channels or ["ntfy", "email"]
    delivered = Notifier(settings).send(
        Message(title="home-tracker: test", body="Notificările funcționează.", tags=["white_check_mark"]),
        channels,
    )
    print("livrat pe: " + (", ".join(delivered) or "niciun canal"))


def cmd_matches(args: argparse.Namespace) -> None:
    """Ultimele apartamente pentru care s-au trimis alerte."""
    settings = load_settings()
    sessions = _setup(settings)
    with sessions() as session:
        rows = session.execute(
            select(Alert, Listing)
            .join(Listing, Alert.listing_id == Listing.id)
            .order_by(Alert.sent_at.desc())
            .limit(args.limit)
        ).all()
        for alert, listing in rows:
            print(
                f"{alert.sent_at:%d.%m %H:%M}  {alert.kind:<10} {format_price(listing.price_eur):>12}  "
                f"{floor_label(listing.floor):<14} {listing.platform:<10} {listing.url}"
            )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hometracker", description="Tracker de piață imobiliară")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("loop", help="rulează periodic (modul din Docker)").set_defaults(func=cmd_loop)

    run = sub.add_parser("run", help="o singură rulare")
    run.add_argument("--catchup", action="store_true", help="citește mai multe pagini")
    run.add_argument("--profile", help="doar acest profil")
    run.set_defaults(func=cmd_run)

    summary = sub.add_parser("summary", help="trimite rezumatul zilnic acum")
    summary.add_argument("--profile", help="doar acest profil")
    summary.add_argument("--print", action="store_true", help="doar afișează ultimele 24 de ore, fără să trimită")
    summary.set_defaults(func=cmd_summary)

    prop = sub.add_parser("property", help="istoricul unui apartament")
    prop.add_argument("reference", help="id-ul apartamentului sau URL-ul unui anunț")
    prop.set_defaults(func=cmd_property)

    web = sub.add_parser("web", help="interfața web (doar citire)")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8080)
    web.set_defaults(func=cmd_web)

    demo = sub.add_parser("demo", help="umple o bază de date goală cu date sintetice")
    demo.add_argument("--apartments", type=int, default=8)
    demo.set_defaults(func=cmd_demo)

    dump = sub.add_parser("dump", help="salvează o pagină și arată ce extrage adapterul")
    dump.add_argument("url")
    dump.set_defaults(func=cmd_dump)

    test = sub.add_parser("test-notify", help="trimite o notificare de test")
    test.add_argument("channels", nargs="*", choices=["ntfy", "email"])
    test.set_defaults(func=cmd_test_notify)

    matches = sub.add_parser("matches", help="ultimele alerte trimise")
    matches.add_argument("--limit", type=int, default=20)
    matches.set_defaults(func=cmd_matches)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
