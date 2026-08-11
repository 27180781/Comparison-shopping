"""Pack-size tests, built from names that actually appear in published files.

docs/04-ALGORITHMS.md asks for calibration against 50+ real names per chain.
This is the start of that set; every name below is either taken verbatim from a
downloaded file or follows a pattern seen in one.

The false-positive cases matter most. A missed multipack costs one comparison.
An invented one makes every price for that product wrong by the pack factor.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from catalog.packsize import normalized_unit_price, parse_pack


# ─── sizes ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,size,measure",
    [
        ("קוטג' תנובה 5% 250 גרם", Decimal("250"), "g"),
        ("לחמניה אנג'ל רגילה 80ג'", Decimal("80"), "g"),
        ("קמח כרמל 1 ק\"ג", Decimal("1000"), "g"),
        ("סוכר לבן 1קילו", Decimal("1000"), "g"),
        ("חלב תנובה 3% 1 ליטר", Decimal("1000"), "ml"),
        ("קוקה קולה 1.5 ליטר", Decimal("1500"), "ml"),
        ("שמן זית 750 מ\"ל", Decimal("750"), "ml"),
        ("משקה 330 מל", Decimal("330"), "ml"),
        ("יוגורט 200 גר'", Decimal("200"), "g"),
    ],
)
def test_size_and_unit_are_normalised_to_grams_or_millilitres(name, size, measure):
    pack = parse_pack(name)
    assert pack.unit_size == size
    assert pack.unit_of_measure == measure


def test_decimal_comma_is_read_as_a_decimal_point():
    assert parse_pack("שמן 1,5 ליטר").unit_size == Decimal("1500")


# ─── packs ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,count",
    [
        ("מארז 4 קוטג' תנובה 250 גרם", 4),
        ("מארז של 6 בירה 330 מל", 6),
        ("6 X 1.5 ליטר קוקה קולה", 6),
        ("קולה 6x1.5 ליטר", 6),
        ("בירה 6*330 מל", 6),
        ("שוקו 8 יח' 200 מל", 8),
    ],
)
def test_multipacks_are_detected(name, count):
    assert parse_pack(name).pack_count == count


def test_base_size_multiplies_pack_by_unit():
    pack = parse_pack("מארז 4 קוטג' תנובה 250 גרם")
    assert pack.base_size == Decimal("1000")


# ─── the cases that must NOT be read as packs ────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        'ניילון נצמד 30ס"מ*30 מטר',  # dimensions, seen in a Shufersal file
        "נטיפי ציפס בטעם שוקולד הנמל 2 ב18.00",  # a promotion phrase in the name
        "קוטג' תנובה 5% 250 גרם",  # plain single item
        "שמפו 400 מל",
    ],
)
def test_things_that_look_like_packs_are_not(name):
    assert parse_pack(name).pack_count == 1


def test_implausible_pack_counts_are_refused():
    """A three-digit multiplier is a model number or a dimension, not a pack."""
    assert parse_pack("מוצר 500 x 250 גרם").pack_count == 1


def test_unparseable_name_degrades_to_a_single_unit():
    for name in (None, "", "   ", "מוצר בלי גודל"):
        pack = parse_pack(name)
        assert pack.pack_count == 1
        assert pack.unit_size is None
        assert pack.unit_of_measure == "unit"


# ─── unit price ──────────────────────────────────────────────────────────────


def test_unit_price_is_per_100g():
    assert normalized_unit_price(Decimal("5.90"), 1, Decimal("250"), "g") == Decimal("2.3600")


def test_unit_price_accounts_for_the_pack():
    """Four 250g tubs for 20.00 is 2.00 per 100g, not 8.00."""
    assert normalized_unit_price(Decimal("20.00"), 4, Decimal("250"), "g") == Decimal("2.0000")


def test_unit_price_falls_back_to_per_unit_without_a_size():
    assert normalized_unit_price(Decimal("12.00"), 3, None, "unit") == Decimal("4.0000")


def test_the_big_pack_can_be_the_expensive_one():
    """The comparison this column exists to make visible."""
    single = normalized_unit_price(Decimal("5.90"), 1, Decimal("250"), "g")
    multipack = normalized_unit_price(Decimal("26.00"), 4, Decimal("250"), "g")
    assert multipack > single


def test_missing_price_yields_no_unit_price():
    assert normalized_unit_price(None, 1, Decimal("250"), "g") is None


def test_zero_size_does_not_divide_by_zero():
    assert normalized_unit_price(Decimal("5.00"), 1, Decimal("0"), "g") is None


def test_measured_item_without_a_size_has_no_comparable_unit_price():
    """Returning a per-unit figure here would mix units inside one column."""
    assert normalized_unit_price(Decimal("5.00"), 1, None, "g") is None
    assert normalized_unit_price(Decimal("5.00"), 1, None, "ml") is None
