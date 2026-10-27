"""Rezumatul zilnic: conținut, goluri în colectare și momentul trimiterii."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from helpers import PROFILE, FakeNotifier

from hometracker.config import Settings
from hometracker.models import Alert, Listing, Property, RunLog, SummaryLog, init_db, make_engine
from hometracker.summary import build_summary, due_period, format_summary, send_due_summaries

# 27 octombrie 2026, ora României e UTC+2 (ora de vară s-a terminat pe 25 octombrie).
NOW = datetime(2026, 10, 27, 18, 30, tzinfo=UTC)  # 20:30 la București
START = NOW - timedelta(hours=24)
STRICT = PROFILE.model_copy(update={"notify": PROFILE.notify.model_copy(update={"min_score": 80})})


@pytest.fixture
def db(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'db.sqlite'}", data_dir=tmp_path)
    return settings, init_db(make_engine(settings.database_url))


def add_listing(session, external_id, *, first_seen, alerted=False, removed_at=None, **fields) -> Listing:
    prop = Property()
    session.add(prop)
    session.flush()
    values = dict(
        platform="storia",
        external_id=external_id,
        url=f"https://storia.test/{external_id}",
        title="Apartament 2 camere Gheorgheni",
        price_eur=150000,
        area_m2=52,
        floor="1",
        balcony=True,
        first_seen=first_seen,
        last_seen=first_seen,
        property_id=prop.id,
        removed_at=removed_at,
        active=removed_at is None,
    )
    values.update(fields)
    listing = Listing(**values)
    session.add(listing)
    session.flush()
    if alerted:
        session.add(Alert(profile=STRICT.name, property_id=prop.id, listing_id=listing.id, kind="new", score=100))
    return listing


def test_summary_lists_weak_matches_and_removed_listings(db):
    settings, sessions = db
    with sessions() as session:
        add_listing(session, "alerted", first_seen=NOW - timedelta(hours=3), alerted=True)
        add_listing(session, "weak", first_seen=NOW - timedelta(hours=5), balcony=False, price_eur=140000)
        add_listing(session, "old", first_seen=NOW - timedelta(days=3), balcony=False)
        add_listing(session, "too-high", first_seen=NOW - timedelta(hours=2), price_eur=190000)
        add_listing(
            session, "sold", first_seen=NOW - timedelta(days=10), alerted=True, removed_at=NOW - timedelta(hours=4)
        )
        add_listing(session, "sold-earlier", first_seen=NOW - timedelta(days=10), removed_at=NOW - timedelta(days=2))
        session.commit()
        summary = build_summary(session, STRICT, START, NOW, settings)
    assert [listing.external_id for listing, _ in summary.weak] == ["weak"]
    assert summary.weak[0][1].score == 75
    assert [listing.external_id for listing in summary.removed] == ["sold"]

    message = format_summary(summary, settings)
    assert message.title == "Rezumat zilnic · test · 1 noi slab potrivite, 1 dispărute"
    assert "Perioada: 26.10 20:30 – 27.10 20:30" in message.body  # ora locală
    assert "140.000 € · etaj 1 · 52 mp · Storia · scor 75" in message.body
    assert "https://storia.test/sold" in message.body
    assert "https://storia.test/too-high" not in message.body


def test_empty_summary_says_so(db):
    settings, sessions = db
    with sessions() as session:
        message = format_summary(build_summary(session, STRICT, START, NOW, settings), settings)
    assert "Nimic altceva nou." in message.body
    assert "Alerte instant trimise: 0 apartamente noi, 0 scăderi de preț." in message.body


def test_collection_gaps_longer_than_two_intervals(db):
    settings, sessions = db  # interval maxim 60 de minute, deci un gol înseamnă peste 2 ore fără rulări
    with sessions() as session:
        for hours_ago in (23, 22, 21, 12, 11.5, 1):
            started = NOW - timedelta(hours=hours_ago)
            session.add(RunLog(profile=STRICT.name, platform="storia", started_at=started, found=1, new=0))
        session.commit()
        summary = build_summary(session, STRICT, START, NOW, settings)
    assert summary.gaps == [
        (NOW - timedelta(hours=21), NOW - timedelta(hours=12)),
        (NOW - timedelta(hours=11.5), NOW - timedelta(hours=1)),
    ]
    assert "Goluri în colectare" in format_summary(summary, settings).body


def test_summary_is_due_once_per_day_after_the_hour(db):
    settings, sessions = db
    with sessions() as session:
        assert due_period(session, STRICT, NOW - timedelta(hours=1), settings) is None  # 19:30 local
        assert due_period(session, STRICT, NOW, settings) == (START, NOW)
        session.add(SummaryLog(profile=STRICT.name, period_start=START, period_end=NOW, sent_at=NOW))
        session.commit()
        assert due_period(session, STRICT, NOW + timedelta(hours=2), settings) is None  # tot 27 octombrie local
        tomorrow = NOW + timedelta(days=1)
        assert due_period(session, STRICT, tomorrow, settings) == (NOW, tomorrow)


def test_late_summary_after_a_long_break_covers_at_most_a_week(db):
    settings, sessions = db
    with sessions() as session:
        old = NOW - timedelta(days=30)
        session.add(SummaryLog(profile=STRICT.name, period_start=old - timedelta(days=1), period_end=old, sent_at=old))
        session.commit()
        start, end = due_period(session, STRICT, NOW, settings)
    assert end - start == timedelta(days=7)


def test_send_due_summaries_records_and_does_not_repeat(db):
    settings, sessions = db
    notifier = FakeNotifier()
    assert send_due_summaries(sessions, [STRICT], notifier, settings, now=NOW) == 1
    assert send_due_summaries(sessions, [STRICT], notifier, settings, now=NOW + timedelta(minutes=40)) == 0
    assert len(notifier.sent) == 1
    with sessions() as session:
        log = session.query(SummaryLog).one()
        assert log.channels == "email"


def test_failed_delivery_is_retried(db):
    settings, sessions = db

    class Silent(FakeNotifier):
        def send(self, message, channels):
            super().send(message, channels)
            return []

    assert send_due_summaries(sessions, [STRICT], Silent(), settings, now=NOW) == 0
    with sessions() as session:
        assert session.query(SummaryLog).count() == 0
    assert send_due_summaries(sessions, [STRICT], FakeNotifier(), settings, now=NOW) == 1


def test_disabled_summary_is_not_sent(db):
    settings, sessions = db
    quiet = STRICT.model_copy(update={"notify": STRICT.notify.model_copy(update={"summary_channels": []})})
    assert send_due_summaries(sessions, [quiet], FakeNotifier(), settings, now=NOW) == 0


def test_long_summary_is_shortened_for_ntfy_only(db):
    settings, sessions = db
    with sessions() as session:
        for i in range(60):
            add_listing(session, f"w{i}", first_seen=NOW - timedelta(hours=2), balcony=False, title="x" * 90)
        session.commit()
    both = STRICT.model_copy(
        update={"notify": STRICT.notify.model_copy(update={"summary_channels": ["email", "ntfy"]})}
    )

    class PerChannel(FakeNotifier):
        def send(self, message, channels):
            self.sent.extend((channel, message) for channel in channels)
            return channels

    notifier = PerChannel()
    send_due_summaries(sessions, [both], notifier, replace(settings), now=NOW)
    bodies = {channel: message.body for channel, message in notifier.sent}
    assert len(bodies["email"].encode()) > 3500
    assert len(bodies["ntfy"].encode()) <= 3510 and bodies["ntfy"].endswith("…")
