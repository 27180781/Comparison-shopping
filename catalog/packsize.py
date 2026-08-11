"""Pull pack count and unit size out of a Hebrew product name.

Why this matters more than it looks: every pack size carries its own barcode,
so a plain barcode join sees "cottage 250g" and "4 x cottage 250g" as unrelated
products. Without this, the comparison misses precisely where discount chains
win, which is the case the whole product exists to answer.

Precision over recall, deliberately. A missed multipack costs one comparison; a
fabricated one ("30 ס\"מ * 30 מטר" read as a pack of 30) makes every price for
that product wrong by 30x, and a wrong number is worse than a missing one.
So a multiplier only counts when it sits immediately before a real size, and
the strings that look like packs but are not -- promotion phrases like
"2 ב-18.00", dimensions like "30*30" -- are left alone.

The expressions started from docs/04-ALGORITHMS.md §2 and were reshaped against
real published names; the doc called them a starting point needing calibration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Unit spellings vary with apostrophe style, quoting and spacing. Grouped by
# what they normalise to, in grams / millilitres / centimetres / units.
GRAM_UNITS = {
    'ק"ג': 1000, "ק'ג": 1000, "קג": 1000, "קילו": 1000, "קילוגרם": 1000,
    "גרם": 1, "גר": 1, "גר'": 1, "ג'": 1, "ג": 1, 'ג"ר': 1,
}
ML_UNITS = {
    "ליטר": 1000, "ל'": 1000, "ליט'": 1000,
    'מ"ל': 1, "מ'ל": 1, "מל": 1, "מיליליטר": 1,
    'סמ"ק': 1, "סמק": 1,
}
UNIT_COUNT = {"יח": 1, "יח'": 1, "יחידות": 1, "יחידה": 1, 'יח"א': 1}

# Longest first so 'ק"ג' is not read as 'ג'.
_ALL_UNITS = {**GRAM_UNITS, **ML_UNITS, **UNIT_COUNT}
_UNIT_ALTERNATION = "|".join(
    re.escape(unit) for unit in sorted(_ALL_UNITS, key=len, reverse=True)
)

NUMBER = r"\d+(?:[.,]\d+)?"
SIZE_RE = re.compile(rf"({NUMBER})\s*({_UNIT_ALTERNATION})(?![\w\"'])")

# "מארז 6", "מארז של 6", "שישיה"
EXPLICIT_PACK_RE = re.compile(r"(?:מארז|מארז\s+של|מבצע\s+מארז)\s*(\d+)")
# "6 יח'" -- a count of units, which is a pack when a size follows
UNIT_PACK_RE = re.compile(rf"(\d+)\s*(?:יח[׳'\"]?|יחידות)")
# "6 x 1.5 ליטר" -- only counts when a size follows immediately
MULTIPLIER_RE = re.compile(rf"(\d+)\s*[xX×*]\s*(?={NUMBER}\s*(?:{_UNIT_ALTERNATION}))")

# Above this a "pack" is far more likely to be a dimension or a model number.
MAX_PLAUSIBLE_PACK = 48

# ILS per 100g / 100ml, so sizes compare across brands.
BASE_QUANTITY = Decimal(100)


@dataclass(frozen=True)
class Pack:
    pack_count: int = 1
    unit_size: Decimal | None = None
    unit_of_measure: str = "unit"

    @property
    def base_size(self) -> Decimal | None:
        if self.unit_size is None:
            return None
        return self.unit_size * self.pack_count


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _measure_for(unit: str) -> str:
    if unit in GRAM_UNITS:
        return "g"
    if unit in ML_UNITS:
        return "ml"
    return "unit"


def parse_pack(name_he: str | None) -> Pack:
    """Extract pack count, unit size and unit of measure from a product name."""
    if not name_he:
        return Pack()

    text = name_he.strip()

    size_match = None
    for candidate in SIZE_RE.finditer(text):
        unit = candidate.group(2)
        if unit in UNIT_COUNT:
            # "6 יח'" is a count, not a size; handled as a pack below.
            continue
        size_match = candidate

    unit_size: Decimal | None = None
    measure = "unit"
    if size_match:
        raw_size = _to_decimal(size_match.group(1))
        unit = size_match.group(2)
        if raw_size is not None:
            unit_size = raw_size * _ALL_UNITS[unit]
            measure = _measure_for(unit)

    return Pack(
        pack_count=_pack_count(text, size_match),
        unit_size=unit_size,
        unit_of_measure=measure,
    )


def _pack_count(text: str, size_match: re.Match[str] | None) -> int:
    """Find the multipack count, refusing anything that smells like a coincidence."""
    explicit = EXPLICIT_PACK_RE.search(text)
    if explicit:
        return _bounded(explicit.group(1))

    # A multiplier only means a pack when it immediately precedes the size we
    # found -- otherwise "30 ס\"מ * 30 מטר" reads as a pack of 30.
    for candidate in MULTIPLIER_RE.finditer(text):
        if size_match and candidate.end() <= size_match.start() + 1:
            return _bounded(candidate.group(1))

    units = UNIT_PACK_RE.search(text)
    if units and size_match:
        return _bounded(units.group(1))

    return 1


def _bounded(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        return 1
    return value if 1 < value <= MAX_PLAUSIBLE_PACK else 1


def normalized_unit_price(
    price: Decimal | float | None,
    pack_count: int = 1,
    unit_size: Decimal | float | None = None,
    unit_of_measure: str = "unit",
) -> Decimal | None:
    """ILS per 100g, per 100ml, or per unit.

    This is the number that makes the comparison honest -- the big pack is
    frequently the expensive one per unit, and only this column shows it.
    """
    if price is None:
        return None
    price = Decimal(str(price))
    packs = pack_count if pack_count and pack_count > 0 else 1

    if unit_of_measure == "unit":
        return (price / packs).quantize(Decimal("0.0001"))

    # Measured in grams or millilitres but the size is missing: there is no
    # comparable number here. Returning a per-unit price instead would put two
    # different units in one column and quietly corrupt every sort by it.
    if not unit_size:
        return None

    total = Decimal(str(unit_size)) * packs
    if total <= 0:
        return None
    return (price / total * BASE_QUANTITY).quantize(Decimal("0.0001"))
