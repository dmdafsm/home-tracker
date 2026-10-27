"""Date sintetice pentru dezvoltarea interfeței: apartamente, anunțuri, prețuri și poze inventate."""

from __future__ import annotations

import io
import random
from datetime import timedelta

from PIL import Image, ImageDraw

from .config import Settings
from .models import Alert, Listing, ListingSnapshot, Photo, Property, init_db, make_engine, utcnow
from .photos import image_hashes, save_image

STREETS = ["Strada Exemplu", "Bulevardul Test", "Aleea Demo", "Strada Model"]


def _picture(seed: int) -> bytes:
    rnd = random.Random(seed)
    image = Image.new("RGB", (480, 360), (230 + rnd.randint(0, 20), 228, 220))
    draw = ImageDraw.Draw(image)
    for _ in range(8):
        x, y = rnd.randint(0, 420), rnd.randint(0, 300)
        color = (rnd.randint(40, 200), rnd.randint(60, 160), rnd.randint(80, 200))
        draw.rectangle([x, y, x + rnd.randint(30, 120), y + rnd.randint(20, 90)], fill=color)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


def create_demo(settings: Settings, apartments: int = 8) -> int:
    """Umple o bază de date goală cu apartamente inventate; întoarce numărul de anunțuri create."""
    sessions = init_db(make_engine(settings.database_url))
    rnd = random.Random(7)
    now = utcnow()
    created = 0
    with sessions() as session:
        if session.query(Listing).count():
            raise RuntimeError("baza de date nu e goală; folosește un DATA_DIR separat pentru date demo")
        for number in range(1, apartments + 1):
            prop = Property()
            session.add(prop)
            session.flush()
            area = rnd.choice([48, 52, 55, 61, 64])
            floor = rnd.choice(["ground", "raised_ground", "1"])
            base_price = rnd.randrange(132_000, 164_000, 1_000)
            started = now - timedelta(days=rnd.randint(2, 70))
            platforms = rnd.sample(["storia", "olx", "imobiliare"], k=rnd.randint(1, 3))
            for index, platform in enumerate(platforms):
                first_seen = started + timedelta(days=index * rnd.randint(0, 6))
                listing = Listing(
                    platform=platform,
                    external_id=f"demo{number}x{index}",
                    url=f"https://example.com/{platform}/demo-{number}-{index}",
                    title=f"Apartament 2 camere, {area} mp, {STREETS[number % len(STREETS)]} (demo)",
                    description=(
                        "Anunț sintetic pentru dezvoltarea interfeței. Bloc de cărămidă, balcon, centrală proprie."
                    ),
                    location_text="Cluj-Napoca, Gheorgheni",
                    price=base_price + index * 2_000,
                    currency="EUR",
                    price_eur=base_price + index * 2_000,
                    area_m2=area,
                    rooms=2,
                    floor=floor,
                    balcony=rnd.random() > 0.3,
                    publisher_type="owner" if index == 0 else "agency",
                    publisher_name=None if index == 0 else f"Agenția Demo {index}",
                    first_seen=first_seen,
                    last_seen=now,
                    detail_fetched=True,
                    property_id=prop.id,
                    match_reason="new" if index == 0 else "score",
                    match_signals=None if index == 0 else "photos:2,area,floor",
                    match_score=None if index == 0 else 1.0,
                    shared_photos=0 if index == 0 else 2,
                )
                if number % 5 == 0:
                    listing.active, listing.removed_at = False, now - timedelta(days=1)
                session.add(listing)
                session.flush()
                if index:
                    listing.matched_listing_id = listing.id - index
                prices = [listing.price_eur]
                if rnd.random() > 0.5:
                    prices.append(listing.price_eur - rnd.choice([3_000, 5_000, 8_000]))
                for step, value in enumerate(prices):
                    session.add(
                        ListingSnapshot(listing=listing, seen_at=first_seen + timedelta(days=step * 5), price_eur=value)
                    )
                listing.price_eur = listing.price = prices[-1]
                for position in range(3):
                    data = _picture(number * 10 + position)
                    hashes = image_hashes(data)
                    path = save_image(settings.photos_dir, platform, listing.external_id, position, data)
                    session.add(
                        Photo(
                            listing=listing,
                            position=position,
                            source_url=f"https://example.com/img/{number}-{position}.jpg",
                            path=str(path),
                            phash=hashes[0] if hashes else None,
                            dhash=hashes[1] if hashes else None,
                        )
                    )
                created += 1
            if number % 2:
                first = session.query(Listing).filter_by(property_id=prop.id).order_by(Listing.id).first()
                session.add(
                    Alert(profile="gheorgheni-mama", property_id=prop.id, listing_id=first.id, kind="new", score=90)
                )
        session.commit()
    return created
