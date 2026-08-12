"""Turn published XML into rows.

Three file kinds, three shapes, one rule: read through the candidate field map
rather than a fixed schema, and let a field that is missing be NULL instead of
guessing. Everything streams.

Nothing here decides what a product *is*. Matching a barcode to a canonical
product, and deciding whether the match is confident enough to compare, belongs
to the catalog stage. Staging is a faithful copy of what the retailer published.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from lxml import etree

from ingestion import fieldmap as fm
from ingestion.xmlstream import iter_elements

# Published filenames carry the store and a timestamp, e.g.
# PriceFull7290027600007-001-036-20260810-030000.xml. Used only as a fallback
# when the document itself omits the field.
FILENAME_DATE = re.compile(r"(20\d{6})")
FILE_KINDS = {
    "pricefull": "price_full",
    "promofull": "promo_full",
    "storesfull": "stores",
    "stores": "stores",
    "store": "stores",
    "price": "price_delta",
    "promo": "promo_delta",
}


def classify(name: str) -> str:
    """Map a published filename to a file kind. Longest prefix wins."""
    lowered = name.lower()
    for prefix in sorted(FILE_KINDS, key=len, reverse=True):
        if lowered.startswith(prefix):
            return FILE_KINDS[prefix]
    return "unknown"


def file_date(path: Path) -> date | None:
    match = FILENAME_DATE.search(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


@dataclass
class FileHeader:
    chain_gov_id: str | None = None
    sub_chain_code: str | None = None
    store_code: str | None = None


@dataclass
class ItemRow:
    store_code: str | None
    sub_chain_code: str | None
    item_code: str | None
    item_type: int | None
    raw_name_he: str | None
    manufacturer: str | None
    unit_qty: str | None
    quantity: Decimal | None
    unit_of_measure: str | None
    is_weighted: bool | None
    price: Decimal | None
    unit_price: Decimal | None
    price_updated_at: datetime | None


@dataclass
class StoreRow:
    store_code: str
    sub_chain_code: str | None
    sub_chain_name: str | None
    name_he: str | None
    address: str | None
    city: str | None
    zip_code: str | None
    store_type: str | None


@dataclass
class PromotionRow:
    promo_code: str | None
    store_code: str | None
    description_he: str | None
    reward_type: str | None
    discount_type: str | None
    discount_rate: Decimal | None
    discounted_price: Decimal | None
    min_qty: Decimal | None
    max_qty: Decimal | None
    min_purchase_amount: Decimal | None
    club_id: str | None
    allow_stacking: bool | None
    starts_at: datetime | None
    ends_at: datetime | None
    item_codes: list[str] = dc_field(default_factory=list)
    gift_item_codes: list[str] = dc_field(default_factory=list)


def read_header(root: etree._Element) -> FileHeader:
    """File-level fields. Present on the root in every chain seen so far."""
    return FileHeader(
        chain_gov_id=fm.direct(root, "chain_id"),
        # Normalised because chains pad these differently between their own
        # files -- see fieldmap.normalize_code.
        sub_chain_code=fm.normalize_code(fm.direct(root, "sub_chain_id")),
        store_code=fm.normalize_code(fm.direct(root, "store_id")),
    )


def iter_items(path: Path) -> Iterator[tuple[FileHeader, ItemRow]]:
    """Stream price rows out of a Price/PriceFull file."""
    for root, element in iter_elements(path, "Item"):
        header = read_header(root)
        yield header, ItemRow(
            store_code=header.store_code,
            sub_chain_code=header.sub_chain_code,
            item_code=fm.direct(element, "item_code"),
            item_type=fm.to_int(fm.direct(element, "item_type")),
            raw_name_he=fm.direct(element, "item_name"),
            manufacturer=fm.direct(element, "manufacturer"),
            unit_qty=fm.direct(element, "unit_qty"),
            quantity=fm.to_quantity(fm.direct(element, "quantity")),
            # Trailing spaces are common here ("100 גרם  ").
            unit_of_measure=(fm.direct(element, "unit_of_measure") or "").strip() or None,
            is_weighted=fm.to_bool(fm.direct(element, "is_weighted")),
            price=fm.to_decimal(fm.direct(element, "item_price")),
            unit_price=fm.to_decimal(fm.direct(element, "unit_of_measure_price")),
            price_updated_at=fm.to_datetime(fm.direct(element, "price_update")),
        )


def iter_stores(path: Path) -> Iterator[tuple[FileHeader, StoreRow]]:
    """Stream store rows.

    The sub chain is an ancestor of the store, not a sibling, and Shufersal
    roots the document at <Chain> while Bina chains use <Root> -- so the sub
    chain is walked up to rather than read off the root.
    """
    for root, element in iter_elements(path, "Store"):
        header = read_header(root)
        sub_chain = element.getparent()
        while sub_chain is not None and not str(sub_chain.tag).startswith("SubChain"):
            sub_chain = sub_chain.getparent()

        store_code = fm.normalize_code(fm.direct(element, "store_id"))
        if not store_code:
            continue

        yield header, StoreRow(
            store_code=store_code,
            sub_chain_code=(
                fm.normalize_code(fm.direct(sub_chain, "sub_chain_id"))
                or header.sub_chain_code
            ),
            sub_chain_name=fm.direct(sub_chain, "sub_chain_name"),
            name_he=fm.direct(element, "store_name"),
            address=fm.direct(element, "address"),
            # Shufersal publishes a numeric code here rather than a city name.
            # Kept raw; Phase 2 geocoding resolves it.
            city=fm.direct(element, "city"),
            zip_code=fm.direct(element, "zip_code"),
            store_type=fm.direct(element, "store_type"),
        )


def _promotion_item_codes(promo: etree._Element) -> tuple[list[str], list[str]]:
    """Collect item codes, separating gifts from purchases.

    Handles both published shapes: PromotionItems/Item on Bina chains and
    Groups/Group/PromotionItems/PromotionItem at Shufersal.
    """
    codes: list[str] = []
    gifts: list[str] = []
    for tag in ("PromotionItem", "Item"):
        for node in promo.iter(tag):
            code = fm.direct(node, "item_code")
            if not code:
                continue
            if fm.to_bool(fm.direct(node, "is_gift")):
                gifts.append(code)
            else:
                codes.append(code)
        if codes or gifts:
            break
    return codes, gifts


def iter_promotions(path: Path) -> Iterator[tuple[FileHeader, PromotionRow]]:
    """Stream promotions.

    Discount fields sit on the promotion for Bina chains and on the individual
    item for Shufersal, so those are read with a deep lookup while identity and
    validity stay shallow.
    """
    for root, element in iter_elements(path, "Promotion"):
        header = read_header(root)
        codes, gifts = _promotion_item_codes(element)

        starts_at = fm.to_datetime(
            fm.direct(element, "promo_start_datetime")
            or fm.direct(element, "promo_start_date"),
            fm.direct(element, "promo_start_hour"),
        )
        ends_at = fm.to_datetime(
            fm.direct(element, "promo_end_datetime") or fm.direct(element, "promo_end_date"),
            fm.direct(element, "promo_end_hour"),
        )

        yield header, PromotionRow(
            promo_code=fm.direct(element, "promo_id"),
            store_code=header.store_code,
            description_he=fm.direct(element, "promo_description"),
            reward_type=fm.deep(element, "reward_type"),
            discount_type=fm.deep(element, "discount_type"),
            discount_rate=fm.to_quantity(fm.deep(element, "discount_rate")),
            discounted_price=fm.to_decimal(fm.deep(element, "discounted_price")),
            min_qty=fm.to_quantity(fm.deep(element, "min_qty")),
            max_qty=fm.to_quantity(fm.deep(element, "max_qty")),
            min_purchase_amount=fm.to_decimal(fm.deep(element, "min_purchase_amount")),
            # Shufersal writes "0 - כלל הלקוחות" rather than a bare id.
            club_id=(fm.deep(element, "club_id") or "").split("-")[0].strip() or None,
            allow_stacking=fm.to_bool(fm.deep(element, "allow_stacking")),
            starts_at=starts_at,
            ends_at=ends_at,
            item_codes=codes,
            gift_item_codes=gifts,
        )
