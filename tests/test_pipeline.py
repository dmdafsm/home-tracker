"""Fluxul complet pe SQLite, cu un adapter fals (fără rețea)."""

from helpers import listing

from hometracker.models import Listing, ListingSnapshot, RunLog


def property_count(sessions) -> int:
    with sessions() as session:
        return len({row.property_id for row in session.query(Listing) if row.property_id is not None})


def test_same_apartment_on_two_platforms_is_one_property(setup):
    catalog, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    catalog["olx"] = [listing("olx", "o1", [2, 3, 4], price_eur=155000)]
    stats = run()
    assert stats.new == 2
    assert property_count(sessions) == 1  # două poze comune → același apartament


def test_different_apartments_stay_separate(setup):
    catalog, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3]), listing("storia", "s2", [7, 8, 9])]
    run()
    assert property_count(sessions) == 2


def test_single_shared_photo_is_not_enough(setup):
    # Aceeași fațadă de bloc, apartamente diferite (etaj diferit).
    catalog, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], floor="1")]
    catalog["olx"] = [listing("olx", "o1", [1, 10, 11], floor="ground")]
    run()
    assert property_count(sessions) == 2


def test_rerun_is_idempotent(setup):
    catalog, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    run()
    stats = run()
    assert stats.new == 0
    with sessions() as session:
        assert session.query(Listing).count() == 1
        assert session.query(ListingSnapshot).count() == 1


def test_price_changes_are_kept_as_snapshots(setup):
    catalog, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=160000)]
    run()
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=150000)]
    run()
    run()  # același preț: niciun snapshot nou
    with sessions() as session:
        assert [s.price_eur for s in session.query(ListingSnapshot).order_by(ListingSnapshot.id)] == [160000, 150000]
        assert session.query(Listing).one().price_eur == 150000


def test_non_matching_listing_is_stored_but_not_grouped(setup):
    catalog, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2], floor="4")]
    run()
    with sessions() as session:
        row = session.query(Listing).one()
        assert row.property_id is None and row.photos == []


def test_empty_source_is_recorded_for_the_status_page(setup):
    _catalog, sessions, run = setup
    run()
    with sessions() as session:
        assert {(r.platform, r.found, r.error) for r in session.query(RunLog)} == {
            ("storia", 0, None),
            ("olx", 0, None),
        }


def test_price_dropping_into_range_groups_the_listing(setup):
    catalog, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=175000)]
    run()
    assert property_count(sessions) == 0
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=162000)]
    run()
    assert property_count(sessions) == 1
