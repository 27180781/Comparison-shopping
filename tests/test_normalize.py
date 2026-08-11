"""Parser tests against fixtures reproducing the real published schemas.

The fixtures carry the disagreements Phase 0 found, not a tidied-up schema:
ItemNm against ItemName, ChainId against ChainID, a <Chain> document root at
Shufersal against <Root> elsewhere, discount fields on the promotion for Bina
chains and on the item at Shufersal, and a gzipped UTF-16 price file.

A parser that only handles the tidy version is the failure mode this whole
suite exists to catch.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from ingestion import normalize

FIXTURES = Path(__file__).parent / "fixtures"


# ─── price files ─────────────────────────────────────────────────────────────


def test_bina_price_file_reads_itemnm_and_space_separated_timestamp():
    rows = list(normalize.iter_items(FIXTURES / "maayan2000_pricefull.xml"))
    assert len(rows) == 3

    header, first = rows[0]
    assert header.chain_gov_id == "7290058159628"
    assert header.store_code == "63"
    assert header.sub_chain_code == "1"

    assert first.item_code == "7290000066318"
    assert first.raw_name_he == "קוטג' תנובה 5% 250 גרם"
    assert first.manufacturer == "תנובה"
    assert first.price == Decimal("5.90")
    assert first.unit_price == Decimal("2.3600")
    assert first.quantity == Decimal("250")
    # Trailing whitespace is common in this field and must not survive.
    assert first.unit_of_measure == "100 גרם"
    assert first.is_weighted is False
    assert first.price_updated_at is not None
    assert first.price_updated_at.tzinfo is not None


def test_shufersal_price_file_is_gzipped_utf16_with_different_field_names():
    rows = list(normalize.iter_items(FIXTURES / "shufersal_pricefull.xml.gz"))
    assert len(rows) == 1

    header, item = rows[0]
    assert header.chain_gov_id == "7290027600007"
    assert header.store_code == "036"

    # ItemName, not ItemNm; ManufactureName, not ManufacturerName.
    assert item.raw_name_he == "קוטג תנובה 5% 250 גרם"
    assert item.manufacturer == "תנובה"
    assert item.price == Decimal("6.20")
    # PriceUpdateTime in ISO-T form, not PriceUpdateDate with a space.
    assert item.price_updated_at is not None
    assert item.price_updated_at.year == 2025


def test_missing_price_is_null_never_zero():
    """A zero price would silently poison every basket total."""
    rows = list(normalize.iter_items(FIXTURES / "maayan2000_pricefull.xml"))
    _, blank = rows[2]
    assert blank.raw_name_he == "מוצר בלי מחיר"
    assert blank.price is None


def test_internal_item_code_survives_parsing_untouched():
    """Staging copies what was published; judging the code is the catalog's job."""
    rows = list(normalize.iter_items(FIXTURES / "maayan2000_pricefull.xml"))
    _, internal = rows[1]
    assert internal.item_code == "5"
    assert internal.item_type == 1  # flagged global despite being internal


# ─── store files ─────────────────────────────────────────────────────────────


def test_shufersal_stores_use_a_chain_root_and_carry_price_group_labels():
    rows = list(normalize.iter_stores(FIXTURES / "shufersal_stores.xml"))
    assert len(rows) == 2

    by_code = {row.store_code: row for _, row in rows}
    shelly = by_code["756"]
    assert shelly.name_he == "שלי באר יעקב"
    assert shelly.address == "17 יצחק שמיר"
    # The sub chain is an ancestor, not a sibling, and this is the price group.
    assert shelly.sub_chain_code == "1"
    assert shelly.sub_chain_name == "שופרסל שלי"
    assert by_code["101"].sub_chain_name == "שופרסל דיל"
    # Shufersal publishes a numeric code where a city name belongs.
    assert shelly.city == "2530"
    assert shelly.zip_code == "7030336"


# ─── promotion files ─────────────────────────────────────────────────────────


def test_bina_promotion_reads_flat_fields_and_nested_club_id():
    rows = list(normalize.iter_promotions(FIXTURES / "maayan2000_promofull.xml"))
    assert len(rows) == 1

    _, promo = rows[0]
    assert promo.promo_code == "253455"
    assert promo.min_qty == Decimal("2")
    assert promo.discounted_price == Decimal("18")
    assert promo.allow_stacking is False
    # ClubId lives under AdditionalRestrictions/Clubs here.
    assert promo.club_id == "0"
    # Date and hour are published separately and must be combined.
    assert promo.starts_at is not None and promo.ends_at is not None
    assert promo.starts_at < promo.ends_at
    assert sorted(promo.item_codes) == ["7290000066318", "835811005131"]


def test_shufersal_promotion_reads_through_group_nesting():
    rows = list(normalize.iter_promotions(FIXTURES / "shufersal_promofull.xml"))
    assert len(rows) == 1

    _, promo = rows[0]
    assert promo.promo_code == "4528444"
    # Discount fields sit on the item, two levels down inside Groups/Group.
    assert promo.min_qty == Decimal("1")
    assert promo.reward_type == "10"
    assert promo.min_purchase_amount == Decimal("99.00")
    # "0 - כלל הלקוחות" is an id with a description glued on.
    assert promo.club_id == "0"
    assert promo.allow_stacking is True
    assert promo.starts_at is not None and promo.ends_at is not None
    # PromotionItem, not Item, and the gift is separated from the purchase.
    assert promo.item_codes == ["7290001780800"]
    assert promo.gift_item_codes == ["7290000066318"]


# ─── filename handling ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected",
    [
        ("PriceFull7290027600007-001-036-20260810-030000.xml", "price_full"),
        ("PromoFull7290058159628-063-202608101002.xml", "promo_full"),
        ("StoresFull7290058159628-000-202605110500.xml", "stores"),
        ("Stores7290027600007-000-20260811-020.xml", "stores"),
        ("Price7290058159628-063-202608101000.gz", "price_delta"),
        ("Promo7290058159628-063-202608101000.gz", "promo_delta"),
        ("whatever.xml", "unknown"),
    ],
)
def test_filename_classification_prefers_the_longest_prefix(name, expected):
    """PriceFull must not be read as a Price delta."""
    assert normalize.classify(name) == expected


def test_file_date_is_read_from_the_published_filename():
    path = Path("PriceFull7290027600007-001-036-20260810-030000.xml")
    assert normalize.file_date(path).isoformat() == "2026-08-10"
