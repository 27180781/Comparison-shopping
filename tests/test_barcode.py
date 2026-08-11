"""Barcode tests, including the real codes Phase 0 pulled out of published files.

docs/04-ALGORITHMS.md flagged the check-digit routine as unverified and told
whoever came next to test it before trusting it. This is that test.
"""

from __future__ import annotations

import pytest

from catalog.barcode import INTERNAL_PREFIXES, is_israeli, normalize_barcode, valid_check_digit


@pytest.mark.parametrize(
    "code",
    [
        "7290000066318",  # Israeli EAN-13, seen in Maayan2000 and Shufersal files
        "4006381333931",  # textbook EAN-13
        "5901234123457",  # textbook EAN-13
        "96385074",  # textbook EAN-8
        "73513537",  # textbook EAN-8
    ],
)
def test_known_good_check_digits(code):
    assert valid_check_digit(code) is True


@pytest.mark.parametrize("code", ["7290000066319", "4006381333932", "96385075", "73513530"])
def test_corrupted_check_digits_are_rejected(code):
    assert valid_check_digit(code) is False


@pytest.mark.parametrize("code", ["", "7", "abcdefghijklm", "72900000663!8"])
def test_check_digit_rejects_non_numeric_and_too_short(code):
    assert valid_check_digit(code) is False


def test_upc_a_becomes_gtin13_by_prefixing_a_zero():
    assert normalize_barcode("036000291452") == "0036000291452"


def test_stripped_leading_zeros_are_restored():
    """Exports drop leading zeros; 11 digits is a GTIN-13 missing two."""
    assert normalize_barcode("36000291452") == "0036000291452"


@pytest.mark.parametrize("prefix", sorted(INTERNAL_PREFIXES))
def test_in_store_prefixes_are_refused_even_with_a_valid_check_digit(prefix):
    """These are valid barcodes that are not globally unique.

    Joining chains on one produces a confident, wrong match -- which is worse
    than reporting the product as not found.
    """
    body = prefix + "0000000000"
    total = sum(d * w for d, w in zip(reversed([int(c) for c in body]), [3, 1] * 7))
    code = body + str((10 - total % 10) % 10)

    assert valid_check_digit(code) is True
    assert normalize_barcode(code) is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "5",  # real ItemCode from a Maayan2000 file, published with ItemType=1
        "6",
        "15",
        "123456",  # no valid length
        "1234567890",
        "not-a-barcode",
    ],
)
def test_non_barcodes_are_rejected(raw):
    assert normalize_barcode(raw) is None


def test_item_type_one_does_not_imply_a_barcode():
    """The measurement that redefined this module's job.

    Every code here was published with ItemType=1. Only one is a barcode.
    """
    published_with_item_type_1 = ["7290000066318", "5", "6", "15"]
    accepted = [code for code in published_with_item_type_1 if normalize_barcode(code)]
    assert accepted == ["7290000066318"]


def test_separators_and_whitespace_are_tolerated():
    assert normalize_barcode(" 729-0000-066318 ") == "7290000066318"


def test_normalisation_is_idempotent():
    once = normalize_barcode("7290000066318")
    assert normalize_barcode(once) == once


def test_israeli_prefix_detection():
    assert is_israeli("7290000066318") is True
    assert is_israeli("4006381333931") is False
