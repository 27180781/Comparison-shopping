"""Geocoding tests.

The unit tests here are about refusing bad answers, because that is the whole
difficulty. A geocoder always returns something; the work is deciding what not
to believe.
"""

from __future__ import annotations

import pytest

from catalog.geocode import (
    REJECT_NO_RESULT,
    REJECT_OUTSIDE_ISRAEL,
    REJECT_TOO_COARSE,
    GeocodeResult,
    StaticGeocoder,
    build_query,
    confirm,
    geocode_stores,
    grade,
    in_israel,
    is_usable_city,
    review_queue,
)
from ingestion.models import Chain, Store
from tests.conftest import requires_db

TLV_ROOFTOP = (32.0853, 34.7818)


class FakeStore:
    """Just the fields build_query reads."""

    def __init__(self, address=None, city=None):
        self.address = address
        self.city = city


# ─── query construction ──────────────────────────────────────────────────────


def test_numeric_city_is_dropped_from_the_query():
    """Shufersal publishes a code where a city name belongs.

    Sending "2530" to a geocoder does not narrow the search, it corrupts it.
    """
    assert is_usable_city("2530") is False
    assert is_usable_city("תל אביב") is True

    query = build_query(FakeStore(address="17 יצחק שמיר", city="2530"))
    assert "2530" not in query
    assert query == "17 יצחק שמיר, ישראל"


def test_a_real_city_is_kept():
    query = build_query(FakeStore(address="דרוק 54", city="ירושלים"))
    assert query == "דרוק 54, ירושלים, ישראל"


def test_a_store_with_no_address_produces_no_query():
    assert build_query(FakeStore()) is None
    assert build_query(FakeStore(address="   ", city="  ")) is None


def test_city_alone_still_produces_a_query():
    assert build_query(FakeStore(city="חיפה")) == "חיפה, ישראל"


# ─── grading ─────────────────────────────────────────────────────────────────


def test_israel_bounds():
    assert in_israel(*TLV_ROOFTOP) is True
    assert in_israel(40.7128, -74.0060) is False  # New York
    assert in_israel(33.8938, 35.5018) is False  # Beirut, just outside


def test_a_result_outside_israel_is_refused_however_confident():
    """The classic silent failure: a Hebrew street name resolving abroad."""
    result = grade(40.7128, -74.0060, "ROOFTOP", False, "google")
    assert result.usable is False
    assert result.rejected_reason == REJECT_OUTSIDE_ISRAEL


def test_a_city_centre_is_refused_for_a_street_query():
    """APPROXIMATE means the provider gave up and returned the locality.

    Accepting it puts every unresolved branch of a chain on one square metre.
    """
    result = grade(*TLV_ROOFTOP, "APPROXIMATE", False, "google")
    assert result.usable is False
    assert result.rejected_reason == REJECT_TOO_COARSE


def test_confidence_tracks_how_precise_the_result_is():
    rooftop = grade(*TLV_ROOFTOP, "ROOFTOP", False, "google")
    interpolated = grade(*TLV_ROOFTOP, "RANGE_INTERPOLATED", False, "google")
    centroid = grade(*TLV_ROOFTOP, "GEOMETRIC_CENTER", False, "google")

    assert rooftop.confidence == 1.0
    assert rooftop.confidence > interpolated.confidence > centroid.confidence
    assert all(result.usable for result in (rooftop, interpolated, centroid))


def test_a_partial_match_is_penalised():
    """Usually the house number was ignored, which is what places a store."""
    exact = grade(*TLV_ROOFTOP, "ROOFTOP", False, "google")
    partial = grade(*TLV_ROOFTOP, "ROOFTOP", True, "google")
    assert partial.confidence < exact.confidence
    assert partial.usable is True  # kept, but it will land in the review queue


# ─── the run ─────────────────────────────────────────────────────────────────

pytestmark_db = requires_db


@pytest.fixture
def stores(session):
    chain = Chain(name_he="שופרסל", scraper_name="SHUFERSAL", portal_type="shufersal")
    session.add(chain)
    session.flush()

    rows = [
        Store(chain_id=chain.id, store_code="036", address="17 יצחק שמיר", city="2530"),
        Store(chain_id=chain.id, store_code="037", address="דרוק 54", city="ירושלים"),
        Store(chain_id=chain.id, store_code="038", address=None, city=None),
    ]
    session.add_all(rows)
    session.commit()
    return rows


@requires_db
def test_geocoding_fills_coordinates_and_skips_stores_without_an_address(session, stores):
    answers = {
        "17 יצחק שמיר, ישראל": grade(31.95, 34.83, "ROOFTOP", False, "static"),
        "דרוק 54, ירושלים, ישראל": grade(31.78, 35.21, "ROOFTOP", False, "static"),
    }
    report = geocode_stores(session, StaticGeocoder(answers))
    session.commit()

    assert report.geocoded == 2
    assert report.no_address == 1
    assert stores[0].lat == 31.95
    assert stores[2].lat is None


@requires_db
def test_a_second_run_costs_nothing(session, stores):
    """Idempotent, so this can run after every ingestion cycle for new branches."""
    answers = {
        "17 יצחק שמיר, ישראל": grade(31.95, 34.83, "ROOFTOP", False, "static"),
        "דרוק 54, ירושלים, ישראל": grade(31.78, 35.21, "ROOFTOP", False, "static"),
    }
    geocoder = StaticGeocoder(answers)
    geocode_stores(session, geocoder)
    session.commit()
    first_pass = len(geocoder.calls)

    geocode_stores(session, geocoder)
    session.commit()
    assert len(geocoder.calls) == first_pass, "already-placed stores must not be looked up again"


@requires_db
def test_the_cache_prevents_paying_twice_for_the_same_address(session, stores):
    """Two chains in one shopping centre publish the same address."""
    answers = {"דרוק 54, ירושלים, ישראל": grade(31.78, 35.21, "ROOFTOP", False, "static")}
    geocoder = StaticGeocoder(answers)

    geocode_stores(session, geocoder)
    session.commit()

    duplicate = Store(
        chain_id=stores[0].chain_id, store_code="900", address="דרוק 54", city="ירושלים"
    )
    session.add(duplicate)
    session.commit()

    before = len(geocoder.calls)
    geocode_stores(session, geocoder)
    session.commit()

    assert len(geocoder.calls) == before, "the repeated address must come from cache"
    assert duplicate.lat == 31.78


@requires_db
def test_a_refused_result_leaves_the_store_unplaced(session, stores):
    """Better no coordinates than coordinates in the wrong country."""
    answers = {
        "17 יצחק שמיר, ישראל": grade(40.7128, -74.0060, "ROOFTOP", False, "static"),
        "דרוק 54, ירושלים, ישראל": grade(31.78, 35.21, "ROOFTOP", False, "static"),
    }
    report = geocode_stores(session, StaticGeocoder(answers))
    session.commit()

    assert report.rejected == 1
    assert stores[0].lat is None


@requires_db
def test_a_refusal_is_cached_so_it_is_not_re_billed(session, stores):
    answers = {"17 יצחק שמיר, ישראל": grade(40.7128, -74.0060, "ROOFTOP", False, "static")}
    geocoder = StaticGeocoder(answers)

    geocode_stores(session, geocoder)
    session.commit()
    before = len(geocoder.calls)

    geocode_stores(session, geocoder)
    session.commit()
    assert len(geocoder.calls) == before


@requires_db
def test_an_address_with_no_result_is_recorded_rather_than_retried(session, stores):
    geocoder = StaticGeocoder({})
    geocode_stores(session, geocoder)
    session.commit()
    before = len(geocoder.calls)

    geocode_stores(session, geocoder)
    session.commit()
    assert len(geocoder.calls) == before


@requires_db
def test_low_confidence_stores_land_in_the_review_queue(session, stores, monkeypatch):
    monkeypatch.setenv("GEOCODE_CONFIDENCE_MIN", "0.8")
    answers = {
        "17 יצחק שמיר, ישראל": grade(31.95, 34.83, "GEOMETRIC_CENTER", False, "static"),
        "דרוק 54, ירושלים, ישראל": grade(31.78, 35.21, "ROOFTOP", False, "static"),
    }
    report = geocode_stores(session, StaticGeocoder(answers))
    session.commit()

    assert report.needs_review == 1
    queue = review_queue(session)
    codes = {entry["store_code"] for entry in queue}
    # The weakly-placed store and the one with no address at all.
    assert codes == {"036", "038"}


@requires_db
def test_confirming_a_store_takes_it_out_of_the_queue(session, stores, monkeypatch):
    monkeypatch.setenv("GEOCODE_CONFIDENCE_MIN", "0.8")
    answers = {"17 יצחק שמיר, ישראל": grade(31.95, 34.83, "GEOMETRIC_CENTER", False, "static")}
    geocode_stores(session, StaticGeocoder(answers))
    session.commit()

    assert confirm(session, stores[0].id, lat=31.9500, lng=34.8300) is True
    session.commit()

    assert stores[0].geocode_verified is True
    assert stores[0].geocode_confidence == 1.0
    assert stores[0].id not in {entry["store_id"] for entry in review_queue(session)}


@requires_db
def test_a_correction_outside_israel_is_refused(session, stores):
    """A typo in a manual fix must not do what the automatic path is stopped from doing."""
    with pytest.raises(ValueError):
        confirm(session, stores[0].id, lat=40.7128, lng=-74.0060)


@requires_db
def test_one_bad_address_does_not_stop_the_run(session, stores):
    class Exploding(StaticGeocoder):
        def lookup(self, query):
            if "יצחק שמיר" in query:
                raise RuntimeError("provider timed out")
            return super().lookup(query)

    geocoder = Exploding({"דרוק 54, ירושלים, ישראל": grade(31.78, 35.21, "ROOFTOP", False, "static")})
    report = geocode_stores(session, geocoder)
    session.commit()

    assert report.errors == 1
    assert report.geocoded == 1
    assert stores[1].lat == 31.78
