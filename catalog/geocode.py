"""Turn published store addresses into coordinates.

Roughly 950 stores, geocoded once and then only as new ones appear. Cheap in
absolute terms, which is exactly why the failure mode to guard against is not
cost but silent wrongness: a geocoder always returns *something*, and a store
placed in the wrong city produces a confident, useless answer to "where is this
cheapest near me".

So every result passes three gates before it is stored:

  1. It must land inside Israel. A geocoder handed a Hebrew street name with no
     usable locality will happily return a same-named street abroad.
  2. It must be a place, not a region. `APPROXIMATE` on a street query means the
     provider fell back to a city centre, which is a store in the wrong place by
     several kilometres.
  3. It must clear the confidence floor, or it goes to the review queue rather
     than into search results.

The published data fights back in one specific way: Shufersal writes a numeric
code in the City field where a name belongs (`2530`). Feeding that to a
geocoder poisons the query, so numeric cities are dropped rather than sent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ingestion.config import _int, _str
from ingestion.models import Chain, Store

log = logging.getLogger(__name__)

# Israel's bounding box, generously drawn. Anything outside is a wrong answer
# regardless of how confident the provider was.
ISRAEL_BOUNDS = (29.3, 33.5, 34.1, 35.95)  # south, north, west, east

# Google's location_type, mapped to how much of a store position it really is.
LOCATION_CONFIDENCE = {
    "ROOFTOP": 1.0,
    "RANGE_INTERPOLATED": 0.85,
    "GEOMETRIC_CENTER": 0.6,
    "APPROXIMATE": 0.3,
}
# A partial match means the provider ignored part of the query to find
# something. Usually the house number, which is what puts a store on a street.
PARTIAL_MATCH_FACTOR = 0.7

REJECT_OUTSIDE_ISRAEL = "outside Israel"
REJECT_TOO_COARSE = "region-level result for a street query"
REJECT_NO_RESULT = "no result"


@dataclass(frozen=True)
class GeocodeResult:
    lat: float
    lng: float
    confidence: float
    provider: str
    formatted_address: str | None = None
    location_type: str | None = None
    partial_match: bool = False
    rejected_reason: str | None = None

    @property
    def usable(self) -> bool:
        return self.rejected_reason is None


class Geocoder(Protocol):
    name: str

    def lookup(self, query: str) -> GeocodeResult | None: ...


def in_israel(lat: float, lng: float) -> bool:
    south, north, west, east = ISRAEL_BOUNDS
    return south <= lat <= north and west <= lng <= east


def confidence_floor() -> float:
    """Below this a store is not trusted for distance search.

    It still exists and its prices are still ingested -- it simply does not
    appear in "near me" results until a human confirms it.
    """
    return float(_str("GEOCODE_CONFIDENCE_MIN", "0.6"))


def is_usable_city(city: str | None) -> bool:
    """Shufersal publishes a numeric code where a city name belongs."""
    if not city:
        return False
    stripped = city.strip()
    return bool(stripped) and not stripped.isdigit()


def build_query(store: Store, chain_name: str | None = None) -> str | None:
    """Compose the string handed to the geocoder.

    The chain name is deliberately left out. "שופרסל שלי" plus a street pushes
    the provider towards a business listing, which is right when the branch is
    listed and badly wrong when a different branch of the same chain is closer
    to that street.
    """
    parts = [part.strip() for part in (store.address, store.city) if part and part.strip()]
    if store.city and not is_usable_city(store.city):
        parts = [part for part in parts if part != store.city.strip()]

    if not parts:
        return None
    return ", ".join(parts) + ", ישראל"


def grade(
    lat: float,
    lng: float,
    location_type: str | None,
    partial_match: bool,
    provider: str,
    formatted_address: str | None = None,
) -> GeocodeResult:
    """Score a provider response and decide whether to keep it."""
    confidence = LOCATION_CONFIDENCE.get((location_type or "").upper(), 0.5)
    if partial_match:
        confidence *= PARTIAL_MATCH_FACTOR

    rejected = None
    if not in_israel(lat, lng):
        rejected = REJECT_OUTSIDE_ISRAEL
    elif (location_type or "").upper() == "APPROXIMATE":
        # A city centre is not a store. Keeping it would put every unmatched
        # branch of a chain on the same square metre.
        rejected = REJECT_TOO_COARSE

    return GeocodeResult(
        lat=lat,
        lng=lng,
        confidence=round(confidence, 3),
        provider=provider,
        formatted_address=formatted_address,
        location_type=location_type,
        partial_match=partial_match,
        rejected_reason=rejected,
    )


class GoogleGeocoder:
    """Google Geocoding API. Region-biased to Israel, Hebrew responses."""

    name = "google"

    def __init__(self, api_key: str, timeout: int = 10, delay_seconds: float = 0.05):
        self.api_key = api_key
        self.timeout = timeout
        # Google allows 50 requests/second; a small delay keeps a 950-store run
        # comfortably inside that without needing a token bucket.
        self.delay_seconds = delay_seconds

    def lookup(self, query: str) -> GeocodeResult | None:
        import requests

        time.sleep(self.delay_seconds)
        response = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={
                "address": query,
                "key": self.api_key,
                "region": "il",
                "language": "he",
                # Confines results rather than merely preferring them, which is
                # what stops a Hebrew street name resolving abroad.
                "components": "country:IL",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()

        status = payload.get("status")
        if status == "ZERO_RESULTS":
            return None
        if status != "OK":
            raise RuntimeError(f"geocoder returned {status}: {payload.get('error_message')}")

        best = payload["results"][0]
        location = best["geometry"]["location"]
        return grade(
            lat=location["lat"],
            lng=location["lng"],
            location_type=best["geometry"].get("location_type"),
            partial_match=bool(best.get("partial_match")),
            provider=self.name,
            formatted_address=best.get("formatted_address"),
        )


class StaticGeocoder:
    """Answers from a dict. Used by tests and for replaying a fixed set."""

    name = "static"

    def __init__(self, answers: dict[str, GeocodeResult]):
        self.answers = answers
        self.calls: list[str] = []

    def lookup(self, query: str) -> GeocodeResult | None:
        self.calls.append(query)
        return self.answers.get(query)


@dataclass
class GeocodeReport:
    considered: int = 0
    from_cache: int = 0
    looked_up: int = 0
    geocoded: int = 0
    rejected: int = 0
    needs_review: int = 0
    no_address: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return self.__dict__.copy()


def geocode_stores(
    session: Session,
    geocoder: Geocoder,
    limit: int | None = None,
    refresh: bool = False,
) -> GeocodeReport:
    """Geocode stores that need it, consulting the cache first.

    Idempotent by default: a store that already has coordinates is skipped, so
    this can run after every ingestion cycle to pick up new branches without
    re-billing for the rest.
    """
    report = GeocodeReport()
    floor = confidence_floor()

    query = select(Store, Chain.name_he).join(Chain, Chain.id == Store.chain_id)
    if not refresh:
        query = query.where(Store.lat.is_(None))
    query = query.order_by(Store.id)
    if limit:
        query = query.limit(limit)

    for store, chain_name in session.execute(query).all():
        report.considered += 1
        lookup_query = build_query(store, chain_name)

        if not lookup_query:
            report.no_address += 1
            continue

        store.geocode_query = lookup_query
        cached = _from_cache(session, lookup_query)

        if cached is not None:
            report.from_cache += 1
            result = cached
        else:
            try:
                result = geocoder.lookup(lookup_query)
                report.looked_up += 1
            except Exception as exc:  # noqa: BLE001 - one bad address must not stop the run
                log.warning("geocoding failed for %r: %s", lookup_query, exc)
                report.errors += 1
                continue

            if result is None:
                result = GeocodeResult(
                    lat=0.0, lng=0.0, confidence=0.0, provider=geocoder.name,
                    rejected_reason=REJECT_NO_RESULT,
                )
            _to_cache(session, lookup_query, result)

        if not result.usable:
            report.rejected += 1
            log.info("refused %r: %s", lookup_query, result.rejected_reason)
            continue

        store.lat = result.lat
        store.lng = result.lng
        store.geocode_confidence = result.confidence
        store.geocoded_at = datetime.now(timezone.utc)
        report.geocoded += 1

        if result.confidence < floor:
            report.needs_review += 1

    return report


def _from_cache(session: Session, query: str) -> GeocodeResult | None:
    row = session.execute(
        text(
            """
            SELECT lat, lng, confidence, provider, formatted_address,
                   location_type, partial_match, rejected_reason
              FROM geocode_cache WHERE query = :query
            """
        ),
        {"query": query},
    ).first()
    if row is None:
        return None
    return GeocodeResult(
        lat=row[0] or 0.0,
        lng=row[1] or 0.0,
        confidence=row[2] or 0.0,
        provider=row[3] or "cache",
        formatted_address=row[4],
        location_type=row[5],
        partial_match=bool(row[6]),
        rejected_reason=row[7],
    )


def _to_cache(session: Session, query: str, result: GeocodeResult) -> None:
    session.execute(
        text(
            """
            INSERT INTO geocode_cache
                (query, lat, lng, confidence, provider, formatted_address,
                 location_type, partial_match, rejected_reason)
            VALUES (:query, :lat, :lng, :confidence, :provider, :formatted_address,
                    :location_type, :partial_match, :rejected_reason)
            ON CONFLICT (query) DO UPDATE SET
                lat = EXCLUDED.lat,
                lng = EXCLUDED.lng,
                confidence = EXCLUDED.confidence,
                provider = EXCLUDED.provider,
                formatted_address = EXCLUDED.formatted_address,
                location_type = EXCLUDED.location_type,
                partial_match = EXCLUDED.partial_match,
                rejected_reason = EXCLUDED.rejected_reason
            """
        ),
        {
            "query": query,
            "lat": result.lat if result.usable else None,
            "lng": result.lng if result.usable else None,
            "confidence": result.confidence,
            "provider": result.provider,
            "formatted_address": result.formatted_address,
            "location_type": result.location_type,
            "partial_match": result.partial_match,
            "rejected_reason": result.rejected_reason,
        },
    )


def review_queue(session: Session, limit: int = 100) -> list[dict]:
    """Stores a human should look at before they are trusted for distance search."""
    floor = confidence_floor()
    rows = session.execute(
        select(
            Store.id,
            Chain.name_he,
            Store.store_code,
            Store.name_he,
            Store.address,
            Store.city,
            Store.lat,
            Store.lng,
            Store.geocode_confidence,
            Store.geocode_query,
        )
        .join(Chain, Chain.id == Store.chain_id)
        .where(
            Store.geocode_verified.is_(False),
            (Store.lat.is_(None)) | (Store.geocode_confidence < floor),
        )
        .order_by(Store.geocode_confidence.nullsfirst(), Store.id)
        .limit(limit)
    ).all()

    return [
        {
            "store_id": row[0],
            "chain": row[1],
            "store_code": row[2],
            "name": row[3],
            "address": row[4],
            "city": row[5],
            "lat": row[6],
            "lng": row[7],
            "confidence": row[8],
            "query": row[9],
        }
        for row in rows
    ]


def confirm(session: Session, store_id: int, lat: float | None = None, lng: float | None = None) -> bool:
    """Mark a store as human-verified, optionally correcting its position."""
    values: dict = {"geocode_verified": True}
    if lat is not None and lng is not None:
        if not in_israel(lat, lng):
            raise ValueError("coordinates are outside Israel")
        values |= {"lat": lat, "lng": lng, "geocode_confidence": 1.0}

    return bool(
        session.execute(update(Store).where(Store.id == store_id).values(**values)).rowcount
    )
