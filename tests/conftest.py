"""Test fixtures.

Database tests run against a real Postgres, never a mock (CLAUDE.md). The
price model is almost entirely SQL -- window functions, partial indexes,
ON CONFLICT -- so a mock would verify the mock rather than the behaviour.

Point TEST_DATABASE_URL at a scratch database; those tests skip without it.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import catalog.models  # noqa: F401  (registers tables on Base)
from ingestion.models import Base, Chain, PriceGroup, Store

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


@pytest.fixture(scope="session")
def engine():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    eng = create_engine(TEST_DATABASE_URL, future=True)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine) -> Session:
    """A clean database per test. Truncate rather than recreate -- far faster."""
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with engine.begin() as conn:
        tables = ",".join(f'"{name}"' for name in reversed(Base.metadata.sorted_tables and
                          [t.name for t in Base.metadata.sorted_tables]))
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    db = factory()
    try:
        yield db
        db.rollback()
    finally:
        db.close()


@pytest.fixture
def seeded(session: Session):
    """Two chains, three stores, one of them in a second price group.

    Shaped after the real data: Shufersal runs several price groups, Bina
    chains run one.
    """
    shufersal = Chain(
        name_he="שופרסל",
        scraper_name="SHUFERSAL",
        portal_type="shufersal",
        gov_chain_ids=["7290027600007"],
    )
    maayan = Chain(
        name_he="מעיין 2000",
        scraper_name="MAAYAN_2000",
        portal_type="bina",
        gov_chain_ids=["7290058159628"],
    )
    session.add_all([shufersal, maayan])
    session.flush()

    shelly = PriceGroup(chain_id=shufersal.id, sub_chain_code="1", label="שופרסל שלי")
    deal = PriceGroup(chain_id=shufersal.id, sub_chain_code="2", label="שופרסל דיל")
    bina_group = PriceGroup(chain_id=maayan.id, sub_chain_code="1")
    session.add_all([shelly, deal, bina_group])
    session.flush()

    stores = [
        Store(chain_id=shufersal.id, price_group_id=shelly.id, store_code="036"),
        Store(chain_id=shufersal.id, price_group_id=shelly.id, store_code="037"),
        Store(chain_id=shufersal.id, price_group_id=deal.id, store_code="101"),
        Store(chain_id=maayan.id, price_group_id=bina_group.id, store_code="63"),
    ]
    session.add_all(stores)
    session.commit()

    return {
        "shufersal": shufersal,
        "maayan": maayan,
        "groups": {"shelly": shelly, "deal": deal, "bina": bina_group},
        "stores": {store.store_code: store for store in stores},
    }


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 11, 3, 0, tzinfo=timezone.utc)
