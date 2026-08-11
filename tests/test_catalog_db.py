"""Catalog and price-history behaviour, against a real Postgres.

These are the assertions that protect the two decisions Phase 0 validated:
that a barcode seen in only one chain must not be comparable, and that
base + exceptions actually stores what it claims to.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select, text

from catalog import build, prices
from catalog.models import CanonicalProduct, PriceBase, PriceCurrent, PriceException, ProductVariant
from ingestion.models import StagingItem
from tests.conftest import requires_db

pytestmark = requires_db

COTTAGE = "7290000066318"
MILK = "4006381333931"
CHAIN_ONLY = "5901234123457"
INTERNAL = "5"


def stage(session, chain_id, store_code, sub_chain, code, name, price, barcode=None):
    session.add(
        StagingItem(
            chain_id=chain_id,
            sub_chain_code=sub_chain,
            store_code=store_code,
            item_code=code,
            barcode=barcode,
            item_type=1,
            raw_name_he=name,
            price=Decimal(price) if price is not None else None,
        )
    )


def test_barcode_in_one_chain_only_never_becomes_comparable(session, seeded, monkeypatch):
    """62.7% of published barcodes look like this, and none of them can be compared.

    A product that exists in one chain has no counterpart -- including it would
    mean comparing it to nothing.
    """
    monkeypatch.setenv("CATALOG_MIN_CHAIN_COUNT", "2")
    shufersal, maayan = seeded["shufersal"], seeded["maayan"]

    stage(session, shufersal.id, "036", "1", COTTAGE, "קוטג 250 גרם", "6.20", COTTAGE)
    stage(session, maayan.id, "63", "1", COTTAGE, "קוטג' תנובה 250 גרם", "5.90", COTTAGE)
    stage(session, shufersal.id, "036", "1", CHAIN_ONLY, "מותג פרטי", "3.00", CHAIN_ONLY)
    session.commit()

    build.build(session)
    session.commit()

    barcodes = set(session.scalars(select(CanonicalProduct.barcode)))
    assert COTTAGE in barcodes
    assert CHAIN_ONLY not in barcodes

    # The variant still exists and is visible -- it is simply not comparable.
    lonely = session.scalar(
        select(ProductVariant).where(ProductVariant.barcode == CHAIN_ONLY)
    )
    assert lonely is not None
    assert lonely.canonical_id is None


def test_unmatched_variants_carry_no_confidence(session, seeded, monkeypatch):
    """An item with no public barcode is never silently folded into a total."""
    monkeypatch.setenv("CATALOG_MIN_CHAIN_COUNT", "2")
    shufersal = seeded["shufersal"]

    stage(session, shufersal.id, "036", "1", INTERNAL, "לחמניה", "2.50", None)
    session.commit()

    build.build(session)
    session.commit()

    variant = session.scalar(select(ProductVariant).where(ProductVariant.item_code == INTERNAL))
    assert variant.canonical_id is None
    assert variant.match_confidence is None


def test_canonical_name_is_the_one_most_chains_use(session, seeded, monkeypatch):
    monkeypatch.setenv("CATALOG_MIN_CHAIN_COUNT", "2")
    shufersal, maayan = seeded["shufersal"], seeded["maayan"]

    stage(session, shufersal.id, "036", "1", MILK, "חלב תנובה 3% 1 ליטר", "7.90", MILK)
    stage(session, maayan.id, "63", "1", MILK, "חלב תנובה 3% 1 ליטר", "7.50", MILK)
    session.commit()

    build.build(session)
    session.commit()

    product = session.scalar(select(CanonicalProduct).where(CanonicalProduct.barcode == MILK))
    assert product.name_he == "חלב תנובה 3% 1 ליטר"
    # Pack parsing runs during promotion, so the unit size is ready for sorting.
    assert product.unit_size == Decimal("1000.000")
    assert product.unit_of_measure == "ml"
    assert product.chain_count == 2


def test_agreeing_stores_produce_one_base_row_and_no_exceptions(session, seeded, monkeypatch):
    """The measured case: 93% of store prices agree with their group."""
    monkeypatch.setenv("CATALOG_MIN_CHAIN_COUNT", "2")
    shufersal, maayan = seeded["shufersal"], seeded["maayan"]

    for store in ("036", "037"):
        stage(session, shufersal.id, store, "1", COTTAGE, "קוטג", "6.20", COTTAGE)
    stage(session, maayan.id, "63", "1", COTTAGE, "קוטג", "5.90", COTTAGE)
    session.commit()

    build.build(session)
    session.commit()
    prices.rebuild(session)
    session.commit()

    shelly_group = seeded["groups"]["shelly"].id
    base_rows = session.scalars(
        select(PriceBase).where(
            PriceBase.price_group_id == shelly_group, PriceBase.valid_to.is_(None)
        )
    ).all()
    assert len(base_rows) == 1
    assert base_rows[0].price == Decimal("6.20")

    exceptions = session.scalar(select(func.count()).select_from(PriceException))
    assert exceptions == 0


def test_a_disagreeing_store_becomes_an_exception(session, seeded, monkeypatch):
    monkeypatch.setenv("CATALOG_MIN_CHAIN_COUNT", "2")
    shufersal, maayan = seeded["shufersal"], seeded["maayan"]

    stage(session, shufersal.id, "036", "1", COTTAGE, "קוטג", "6.20", COTTAGE)
    stage(session, shufersal.id, "037", "1", COTTAGE, "קוטג", "6.20", COTTAGE)
    # This store disagrees with its group.
    stage(session, shufersal.id, "101", "2", COTTAGE, "קוטג", "5.50", COTTAGE)
    stage(session, maayan.id, "63", "1", COTTAGE, "קוטג", "5.90", COTTAGE)
    session.commit()

    build.build(session)
    session.commit()
    prices.rebuild(session)
    session.commit()

    deal_store = seeded["stores"]["101"].id
    current = session.scalar(
        select(PriceCurrent).where(PriceCurrent.store_id == deal_store)
    )
    assert current.price == Decimal("5.50")

    shelly_store = seeded["stores"]["036"].id
    assert session.scalar(
        select(PriceCurrent.price).where(PriceCurrent.store_id == shelly_store)
    ) == Decimal("6.20")


def test_a_price_change_closes_the_old_row_and_opens_a_new_one(session, seeded, monkeypatch, now):
    """SCD2 is what lets the system answer 'was this ever actually cheaper?'."""
    monkeypatch.setenv("CATALOG_MIN_CHAIN_COUNT", "2")
    shufersal, maayan = seeded["shufersal"], seeded["maayan"]

    stage(session, shufersal.id, "036", "1", COTTAGE, "קוטג", "6.20", COTTAGE)
    stage(session, maayan.id, "63", "1", COTTAGE, "קוטג", "5.90", COTTAGE)
    session.commit()
    build.build(session)
    session.commit()
    prices.rebuild(session, now=now)
    session.commit()

    session.execute(text("DELETE FROM staging_items"))
    stage(session, shufersal.id, "036", "1", COTTAGE, "קוטג", "7.40", COTTAGE)
    stage(session, maayan.id, "63", "1", COTTAGE, "קוטג", "5.90", COTTAGE)
    session.commit()
    prices.rebuild(session, now=now + timedelta(days=1))
    session.commit()

    group = seeded["groups"]["shelly"].id
    history = session.scalars(
        select(PriceBase)
        .where(PriceBase.price_group_id == group)
        .order_by(PriceBase.valid_from)
    ).all()

    assert len(history) == 2
    assert history[0].price == Decimal("6.20")
    assert history[0].valid_to is not None  # closed
    assert history[1].price == Decimal("7.40")
    assert history[1].valid_to is None  # open

    open_rows = [row for row in history if row.valid_to is None]
    assert len(open_rows) == 1, "exactly one row may be open per (group, variant)"


def test_unchanged_price_does_not_churn_history(session, seeded, monkeypatch, now):
    """Re-ingesting the same prices must not create a new row every night."""
    monkeypatch.setenv("CATALOG_MIN_CHAIN_COUNT", "2")
    shufersal, maayan = seeded["shufersal"], seeded["maayan"]

    for _ in range(2):
        session.execute(text("DELETE FROM staging_items"))
        stage(session, shufersal.id, "036", "1", COTTAGE, "קוטג", "6.20", COTTAGE)
        stage(session, maayan.id, "63", "1", COTTAGE, "קוטג", "5.90", COTTAGE)
        session.commit()
        build.build(session)
        session.commit()
        prices.rebuild(session, now=now)
        session.commit()

    assert session.scalar(select(func.count()).select_from(PriceBase)) == 2  # one per group


def test_normalized_unit_price_is_materialised_for_sorting(session, seeded, monkeypatch):
    monkeypatch.setenv("CATALOG_MIN_CHAIN_COUNT", "2")
    shufersal, maayan = seeded["shufersal"], seeded["maayan"]

    stage(session, shufersal.id, "036", "1", COTTAGE, "קוטג תנובה 250 גרם", "6.20", COTTAGE)
    stage(session, maayan.id, "63", "1", COTTAGE, "קוטג תנובה 250 גרם", "5.90", COTTAGE)
    session.commit()

    build.build(session)
    session.commit()
    prices.rebuild(session)
    session.commit()

    unit_price = session.scalar(
        select(PriceCurrent.normalized_unit_price).where(
            PriceCurrent.store_id == seeded["stores"]["036"].id
        )
    )
    assert unit_price == Decimal("2.4800")  # 6.20 per 250g -> per 100g


def test_missing_price_never_reaches_price_current(session, seeded, monkeypatch):
    """A NULL price must stay absent rather than becoming a zero."""
    monkeypatch.setenv("CATALOG_MIN_CHAIN_COUNT", "2")
    shufersal, maayan = seeded["shufersal"], seeded["maayan"]

    stage(session, shufersal.id, "036", "1", COTTAGE, "קוטג", None, COTTAGE)
    stage(session, maayan.id, "63", "1", COTTAGE, "קוטג", "5.90", COTTAGE)
    session.commit()

    build.build(session)
    session.commit()
    prices.rebuild(session)
    session.commit()

    rows = session.scalars(
        select(PriceCurrent).where(PriceCurrent.store_id == seeded["stores"]["036"].id)
    ).all()
    assert rows == []
    assert session.scalar(select(func.count()).where(PriceCurrent.price == 0)) in (0, None)
