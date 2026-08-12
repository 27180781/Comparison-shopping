"""API tests against a real database.

The trust rules are asserted here rather than left to the frontend: every price
carries an update stamp, every response carries the register-wins disclaimer
and the scope note, missing items are named, and the count of promotions left
out is present. Those are non-negotiable (ADR-010), so they are tested like
behaviour, not documented like intentions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from catalog.models import (
    CanonicalProduct,
    PriceCurrent,
    PriceDaily,
    ProductVariant,
    Promotion,
    PromotionItem,
)
from ingestion.models import Chain, PriceGroup, Store
from tests.conftest import requires_db

pytestmark = requires_db

# Two stores in Tel Aviv, one far away in Haifa.
TLV = (32.0853, 34.7818)
NEARBY = (32.0900, 34.7900)
HAIFA = (32.7940, 34.9896)


@pytest.fixture
def client(engine, session, monkeypatch):
    """App wired to the test database."""
    import ingestion.db as db_module
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(db_module, "session_factory", lambda: factory)

    from api.main import app

    return TestClient(app)


@pytest.fixture
def catalog(session):
    """One product priced in three stores, with a promotion in one of them."""
    chain = Chain(name_he="שופרסל", scraper_name="SHUFERSAL", portal_type="shufersal")
    other = Chain(name_he="מעיין 2000", scraper_name="MAAYAN_2000", portal_type="bina")
    session.add_all([chain, other])
    session.flush()

    group = PriceGroup(chain_id=chain.id, sub_chain_code="1", label="שופרסל שלי")
    session.add(group)
    session.flush()

    stores = {
        "near": Store(chain_id=chain.id, price_group_id=group.id, store_code="036",
                      name_he="שלי תל אביב", lat=TLV[0], lng=TLV[1], city="תל אביב"),
        "also_near": Store(chain_id=other.id, store_code="63", name_he="מעיין תל אביב",
                           lat=NEARBY[0], lng=NEARBY[1], city="תל אביב"),
        "far": Store(chain_id=chain.id, price_group_id=group.id, store_code="900",
                     name_he="שלי חיפה", lat=HAIFA[0], lng=HAIFA[1], city="חיפה"),
    }
    session.add_all(stores.values())
    session.flush()

    cottage = CanonicalProduct(
        barcode="7290000066318", name_he="קוטג' תנובה 5% 250 גרם",
        pack_count=1, unit_size=Decimal("250"), unit_of_measure="g", chain_count=2,
    )
    milk = CanonicalProduct(
        barcode="4006381333931", name_he="חלב תנובה 3% 1 ליטר",
        pack_count=1, unit_size=Decimal("1000"), unit_of_measure="ml", chain_count=2,
    )
    session.add_all([cottage, milk])
    session.flush()

    variants = {}
    for key, store in stores.items():
        for product in (cottage, milk):
            variant = ProductVariant(
                canonical_id=product.id, chain_id=store.chain_id,
                item_code=f"{product.barcode}-{store.store_code}",
                barcode=product.barcode, raw_name_he=product.name_he,
                match_method="barcode", match_confidence=1.0,
            )
            session.add(variant)
            session.flush()
            variants[(key, product.id)] = variant

    now = datetime.now(timezone.utc)
    prices = {
        ("near", cottage.id): "6.20", ("near", milk.id): "7.90",
        ("also_near", cottage.id): "5.90", ("also_near", milk.id): "9.50",
        ("far", cottage.id): "4.00", ("far", milk.id): "4.00",
    }
    for (key, product_id), price in prices.items():
        session.add(
            PriceCurrent(
                store_id=stores[key].id,
                variant_id=variants[(key, product_id)].id,
                canonical_id=product_id,
                price=Decimal(price),
                normalized_unit_price=Decimal(price) / Decimal("2.5"),
                updated_at=now,
            )
        )

    promo = Promotion(
        chain_id=other.id, store_id=stores["also_near"].id, promo_code="P1",
        description_he="2 ב-10", promo_kind="min_qty", min_qty=Decimal(2),
        discounted_price=Decimal("10.00"), applicable_v1=True,
        parse_status="structured", starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=30), club_id="0",
    )
    skipped = Promotion(
        chain_id=other.id, store_id=stores["also_near"].id, promo_code="P2",
        description_he="קנה ב-99 וקבל מתנה", promo_kind="threshold",
        min_purchase_amount=Decimal("99"), applicable_v1=False,
        parse_status="structured", starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=30),
    )
    session.add_all([promo, skipped])
    session.flush()
    session.add_all([
        PromotionItem(promotion_id=promo.id, variant_id=variants[("also_near", cottage.id)].id),
        PromotionItem(promotion_id=skipped.id, variant_id=variants[("also_near", cottage.id)].id),
    ])

    for offset in range(10):
        session.add(
            PriceDaily(
                canonical_id=cottage.id,
                day=(now - timedelta(days=offset)).date(),
                min_price=Decimal("7.00"), max_price=Decimal("8.00"),
                avg_price=Decimal("7.50"), store_count=3,
            )
        )
    session.commit()
    return {
        "stores": stores,
        "cottage": cottage,
        "milk": milk,
        "chain": chain,
        "other": other,
    }


# ─── health ──────────────────────────────────────────────────────────────────


def test_health_reports_database_and_catalog_state(client, catalog):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["database"] is True
    assert body["products"] == 2


def test_health_names_chains_that_stopped_reporting(client, catalog):
    """A chain failing silently looks exactly like a quiet one."""
    body = client.get("/health").json()
    assert set(body["stale_chains"]) == {"שופרסל", "מעיין 2000"}


# ─── search ──────────────────────────────────────────────────────────────────


def test_search_finds_a_product_by_hebrew_name(client, catalog):
    body = client.get("/search", params={"q": "קוטג"}).json()
    assert body["total"] >= 1
    assert "קוטג" in body["results"][0]["product"]["name_he"]


def test_search_tolerates_a_misspelling(client, catalog):
    """Chains spell the same product differently; exact matching is useless."""
    body = client.get("/search", params={"q": "קוטג תנובה"}).json()
    assert body["total"] >= 1


def test_every_response_carries_the_disclaimer_and_the_scope_note(client, catalog):
    body = client.get("/search", params={"q": "קוטג"}).json()
    assert "המחיר בקופה גובר" in body["disclaimer"]
    assert "מותגים מובילים" in body["scope_note"]


def test_every_price_carries_an_update_stamp(client, catalog):
    body = client.get("/search", params={"q": "קוטג"}).json()
    prices = body["results"][0]["prices"]
    assert prices
    assert all(price["updated_at"] for price in prices)


def test_radius_excludes_stores_that_are_too_far(client, catalog):
    """Haifa is ~90km from Tel Aviv and must not appear in a 10km search."""
    body = client.get(
        "/search", params={"q": "קוטג", "lat": TLV[0], "lng": TLV[1], "radius_km": 10}
    ).json()
    cities = {price["store"]["city"] for price in body["results"][0]["prices"]}
    assert cities == {"תל אביב"}

    wide = client.get(
        "/search", params={"q": "קוטג", "lat": TLV[0], "lng": TLV[1], "radius_km": 100}
    ).json()
    assert "חיפה" in {price["store"]["city"] for price in wide["results"][0]["prices"]}


def test_results_sort_by_price_per_unit_by_default(client, catalog):
    """Sticker price hides the case where the big pack is dearer."""
    body = client.get(
        "/search", params={"q": "קוטג", "lat": TLV[0], "lng": TLV[1], "radius_km": 100}
    ).json()
    unit_prices = [
        Decimal(price["normalized_unit_price"])
        for price in body["results"][0]["prices"]
        if price["normalized_unit_price"] is not None
    ]
    assert unit_prices == sorted(unit_prices)


def test_only_applicable_promotions_are_badged(client, catalog):
    """Flagging a threshold offer v1 will not honour promises a discount that never arrives."""
    body = client.get(
        "/search", params={"q": "קוטג", "lat": TLV[0], "lng": TLV[1], "radius_km": 10}
    ).json()
    badged = [p for p in body["results"][0]["prices"] if p["has_promotion"]]
    assert len(badged) == 1
    assert badged[0]["promotion_description"] == "2 ב-10"


def test_search_with_no_match_returns_empty_rather_than_guessing(client, catalog):
    body = client.get("/search", params={"q": "מוצרשלאקיים"}).json()
    assert body["total"] == 0
    assert body["results"] == []


# ─── history ─────────────────────────────────────────────────────────────────


def test_history_compares_today_against_the_running_average(client, catalog):
    """The question no shopping app answers: was it ever anything else?"""
    product_id = catalog["cottage"].id
    body = client.get(f"/products/{product_id}/history").json()
    assert len(body["points"]) == 10
    assert body["average"] == "7.50"
    # Cheapest right now is 4.00 against a 7.50 average.
    assert body["verdict"] == "נמוך מהרגיל"


def test_history_for_an_unknown_product_is_a_404(client, catalog):
    assert client.get("/products/999999/history").status_code == 404


# ─── basket ──────────────────────────────────────────────────────────────────


def test_basket_picks_the_cheapest_reachable_store(client, catalog):
    body = client.post(
        "/basket/optimize",
        json={
            "items": [{"canonical_id": catalog["cottage"].id, "qty": 1}],
            "lat": TLV[0], "lng": TLV[1], "radius_km": 10, "mode": "cheapest",
        },
    ).json()
    assert body["single_store"] is not None
    assert body["stores_considered"] >= 2
    assert Decimal(body["single_store"]["goods_total"]) == Decimal("5.90")


def test_basket_applies_a_quantity_promotion(client, catalog):
    """Two units at 5.90 is 11.80; the store's "2 for 10" must win."""
    body = client.post(
        "/basket/optimize",
        json={
            "items": [{"canonical_id": catalog["cottage"].id, "qty": 2}],
            "lat": TLV[0], "lng": TLV[1], "radius_km": 10, "mode": "cheapest",
        },
    ).json()
    assert Decimal(body["single_store"]["goods_total"]) == Decimal("10.00")
    assert body["single_store"]["applied_promotions"] == 1


def test_every_result_reports_promotions_it_could_not_apply(client, catalog):
    """The transparency rule, asserted rather than trusted."""
    body = client.post(
        "/basket/optimize",
        json={
            "items": [{"canonical_id": catalog["cottage"].id, "qty": 2}],
            "lat": TLV[0], "lng": TLV[1], "radius_km": 10, "mode": "cheapest",
        },
    ).json()
    assert body["single_store"]["skipped_promotions"] >= 1


def test_products_nobody_stocks_are_named_not_dropped(client, catalog, session):
    orphan = CanonicalProduct(barcode="5901234123457", name_he="מוצר שאף אחד לא מוכר", chain_count=2)
    session.add(orphan)
    session.commit()

    body = client.post(
        "/basket/optimize",
        json={
            "items": [
                {"canonical_id": catalog["cottage"].id, "qty": 1},
                {"canonical_id": orphan.id, "qty": 1},
            ],
            "lat": TLV[0], "lng": TLV[1], "radius_km": 10,
        },
    ).json()
    assert [p["canonical_id"] for p in body["missing_products"]] == [orphan.id]


def test_single_store_mode_never_returns_a_split(client, catalog):
    body = client.post(
        "/basket/optimize",
        json={
            "items": [
                {"canonical_id": catalog["cottage"].id, "qty": 1},
                {"canonical_id": catalog["milk"].id, "qty": 1},
            ],
            "lat": TLV[0], "lng": TLV[1], "radius_km": 10, "mode": "single_store",
        },
    ).json()
    assert body["split"] is None


def test_a_split_reports_both_the_saving_and_the_extra_time(client, catalog):
    """The user decides whether the money is worth the minutes."""
    body = client.post(
        "/basket/optimize",
        json={
            "items": [
                {"canonical_id": catalog["cottage"].id, "qty": 1},
                {"canonical_id": catalog["milk"].id, "qty": 1},
            ],
            "lat": TLV[0], "lng": TLV[1], "radius_km": 10, "mode": "cheapest",
        },
    ).json()
    if body["split"] is not None:
        assert body["split_saving"] is not None
        assert body["split_extra_minutes"] is not None
        assert Decimal(body["split"]["goods_total"]) <= Decimal(
            body["single_store"]["goods_total"]
        )


def test_basket_rejects_an_empty_request(client):
    assert client.post("/basket/optimize", json={"items": [], "lat": 32.0, "lng": 34.7}).status_code == 422


# ─── catalogue browsing ────────────────────────────────────────────────────


def test_browse_ranks_by_the_gap_between_cheapest_and_dearest(client, catalog):
    """The default order is the whole product thesis: biggest gap first."""
    body = client.get("/products?min_chains=1").json()
    spreads = [Decimal(row["spread"]) for row in body["results"]]
    assert spreads == sorted(spreads, reverse=True)

    milk = next(r for r in body["results"] if r["product"]["canonical_id"] == catalog["milk"].id)
    # 4.00 in Haifa against 9.50 in Tel Aviv.
    assert Decimal(milk["cheapest_price"]) == Decimal("4.00")
    assert Decimal(milk["dearest_price"]) == Decimal("9.50")
    assert Decimal(milk["spread"]) == Decimal("5.50")


def test_browse_names_the_chain_at_each_end(client, catalog):
    """A range without the chains attached is not actionable."""
    row = next(
        r
        for r in client.get("/products?min_chains=1").json()["results"]
        if r["product"]["canonical_id"] == catalog["milk"].id
    )
    assert row["cheapest_chain"] == "שופרסל"
    assert row["dearest_chain"] == "מעיין 2000"


def test_filtering_by_chain_restricts_the_prices_it_compares(client, catalog):
    """Not just which products are listed -- which prices form the range."""
    only_maayan = client.get(
        f"/products?min_chains=1&chain_ids={catalog['other'].id}"
    ).json()
    row = next(
        r for r in only_maayan["results"] if r["product"]["canonical_id"] == catalog["milk"].id
    )
    assert row["cheapest_chain"] == "מעיין 2000"
    assert Decimal(row["cheapest_price"]) == Decimal("9.50")
    assert row["priced_chain_count"] == 1


def test_promo_only_returns_only_products_with_an_applicable_promotion(client, catalog):
    body = client.get("/products?min_chains=1&promo_only=true").json()
    ids = {row["product"]["canonical_id"] for row in body["results"]}
    # The threshold promotion on the same product is not applicable in v1, but
    # the cottage cheese also carries one that is.
    assert ids == {catalog["cottage"].id}


def test_max_price_filters_on_the_cheapest_price(client, catalog):
    body = client.get("/products?min_chains=1&max_price=4.50").json()
    assert all(Decimal(row["cheapest_price"]) <= Decimal("4.50") for row in body["results"])


def test_browse_paginates_without_losing_the_total(client, catalog):
    body = client.get("/products?min_chains=1&page_size=1").json()
    assert len(body["results"]) == 1
    assert body["total"] >= 2
    assert body["page"] == 1


def test_filters_are_read_from_the_data_not_hardcoded(client, catalog):
    body = client.get("/filters").json()
    assert {chain["label"] for chain in body["chains"]} == {"שופרסל", "מעיין 2000"}
    assert {unit["value"] for unit in body["units"]} == {"g", "ml"}


def test_product_prices_returns_the_same_table_without_a_search(client, catalog):
    rows = client.get(f"/products/{catalog['cottage'].id}/prices").json()
    assert len(rows) == 3
    assert [Decimal(row["price"]) for row in rows] == sorted(
        Decimal(row["price"]) for row in rows
    )
    # Every price still carries its stamp, search or no search.
    assert all(row["updated_at"] for row in rows)


def test_product_prices_404s_for_an_unknown_product(client, catalog):
    assert client.get("/products/999999/prices").status_code == 404
