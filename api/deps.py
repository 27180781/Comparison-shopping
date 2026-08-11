"""Shared dependencies and tunables for the API layer."""

from __future__ import annotations

from decimal import Decimal
from typing import Iterator

from sqlalchemy.orm import Session

from ingestion.config import _int, _str
from ingestion.db import session_factory


def get_session() -> Iterator[Session]:
    session = session_factory()()
    try:
        yield session
    finally:
        session.close()


def default_radius_km() -> float:
    return float(_str("DEFAULT_SEARCH_RADIUS_KM", "10"))


def travel_penalty_per_stop() -> Decimal:
    return Decimal(_str("TRAVEL_PENALTY_PER_STOP_ILS", "15"))


def travel_value_per_hour() -> Decimal:
    return Decimal(_str("TRAVEL_TIME_VALUE_ILS_PER_HOUR", "40"))


def search_result_limit() -> int:
    return _int("SEARCH_RESULT_LIMIT", 20)


def stores_per_product_limit() -> int:
    return _int("STORES_PER_PRODUCT_LIMIT", 25)


def trigram_threshold() -> float:
    """How wrong a spelling may be and still match.

    Hebrew product names vary between chains in spacing, quoting and
    abbreviation, so exact matching is useless and a low bar returns noise.
    """
    return float(_str("SEARCH_TRIGRAM_THRESHOLD", "0.25"))


TRAVEL_MODES = {
    # The user picks the trade-off; the system only shows it.
    "cheapest": (Decimal("0"), Decimal("0")),
    "balanced": None,  # resolved from env at request time
    "single_store": (Decimal("0"), Decimal("0")),
}


def travel_settings(mode: str) -> tuple[Decimal, Decimal]:
    if mode == "balanced":
        return travel_penalty_per_stop(), travel_value_per_hour()
    return TRAVEL_MODES.get(mode) or (Decimal("0"), Decimal("0"))
