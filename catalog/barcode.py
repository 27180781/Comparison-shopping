"""Barcode normalisation -- the filter the whole v1 catalog rests on.

Phase 0 measurement #1 changed what this is for. The specification assumed
`ItemType=1` marked a global barcode and that this function refined it; the
data showed ItemType is 1 on 99.99% of items, including codes like "5". It
carries no information. So this is not a refinement of the published flag, it
*is* the filter, and 97.86% of published items pass it.

Prefixes 02 and 20-29 are reserved for in-store use. They are perfectly valid
barcodes and they are not globally unique, so joining two chains on one
produces confident nonsense -- a store-packed chicken in one chain matched to
a wheel of cheese in another.
"""

from __future__ import annotations

INTERNAL_PREFIXES = frozenset({"02"} | {str(n) for n in range(20, 30)})


def valid_check_digit(code: str) -> bool:
    """GS1 check digit for EAN-13 and EAN-8.

    Weights alternate 3,1 from the rightmost body digit in both lengths, which
    is why one expression covers both. Verified against known-good and
    deliberately corrupted codes in tests/test_barcode.py.
    """
    if not code.isdigit() or len(code) < 2:
        return False
    digits = [int(char) for char in code]
    body, check = digits[:-1], digits[-1]
    total = sum(digit * weight for digit, weight in zip(reversed(body), [3, 1] * 7))
    return (10 - total % 10) % 10 == check


def normalize_barcode(raw: str | None) -> str | None:
    """Return a normalised EAN-13/EAN-8, or None if this is not a public barcode.

    None means "do not compare this across chains". It does not mean the item
    is invalid -- weighted goods and private label carry real internal codes and
    are simply outside v1.
    """
    if not raw:
        return None

    code = "".join(char for char in raw if char.isdigit())
    if not code:
        return None

    if len(code) == 12:
        # UPC-A. The GTIN-13 form is the same code with a leading zero.
        code = "0" + code
    elif len(code) in (11, 13):
        # Exports routinely drop leading zeros, so 11 digits is a GTIN-13 that
        # lost two of them rather than a distinct code.
        code = code.zfill(13)
    elif len(code) not in (8, 13):
        return None

    if code[:2] in INTERNAL_PREFIXES:
        return None
    if not valid_check_digit(code):
        return None
    return code


def is_israeli(barcode: str) -> bool:
    """729 is the GS1 prefix issued to Israel. Informational, not a filter."""
    return barcode.startswith("729")
