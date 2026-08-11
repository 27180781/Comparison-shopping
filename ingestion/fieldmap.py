"""Where each chain hides each field.

Derived from real files with scripts/phase0_schema.py, not from the
documentation -- which is wrong on several names. Observed disagreements:

    concept        Bina chains        Shufersal
    ------------------------------------------------------
    item name      ItemNm             ItemName
    manufacturer   ManufacturerName   ManufactureName
    chain id       ChainId            ChainID
    price stamp    PriceUpdateDate    PriceUpdateTime
    min offered    MinNoOfItemOfered  MinNoOfItemOffered
    stores root    Root               Chain
    club           AdditionalRestrictions/Clubs/ClubId   ClubID

Lookup is by candidate list rather than a per-chain table on purpose: a chain
nobody has looked at yet degrades to "field missing" instead of being silently
mis-parsed, and adding a newly-observed spelling is one entry rather than a new
branch.

Date formats differ too -- "2026-08-10 10:00:00" against
"2026-08-10T10:00:00.000" -- so parsing tries several and gives up to NULL
rather than guessing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from lxml import etree

TZ = ZoneInfo("Asia/Jerusalem")

# Ordered by observed frequency; first non-empty hit wins.
CANDIDATES: dict[str, tuple[str, ...]] = {
    # file-level
    "chain_id": ("ChainId", "ChainID"),
    "sub_chain_id": ("SubChainId", "SubChainID"),
    "sub_chain_name": ("SubChainName",),
    "store_id": ("StoreId", "StoreID"),
    "chain_name": ("ChainName",),
    # item-level
    "item_code": ("ItemCode",),
    "item_type": ("ItemType",),
    "item_name": ("ItemNm", "ItemName"),
    "manufacturer": ("ManufacturerName", "ManufactureName"),
    "manufacture_country": ("ManufactureCountry",),
    "unit_qty": ("UnitQty",),
    "quantity": ("Quantity",),
    "unit_of_measure": ("UnitOfMeasure",),
    "is_weighted": ("bIsWeighted",),
    "qty_in_package": ("QtyInPackage",),
    "item_price": ("ItemPrice",),
    "unit_of_measure_price": ("UnitOfMeasurePrice",),
    "price_update": ("PriceUpdateDate", "PriceUpdateTime"),
    "item_status": ("ItemStatus",),
    # store-level
    "store_name": ("StoreName",),
    "address": ("Address",),
    "city": ("City",),
    "zip_code": ("ZipCode", "ZIPCode"),
    "store_type": ("StoreType",),
    # promotion-level
    "promo_id": ("PromotionId", "PromotionID"),
    "promo_description": ("PromotionDescription",),
    "promo_start_date": ("PromotionStartDate",),
    "promo_end_date": ("PromotionEndDate",),
    "promo_start_hour": ("PromotionStartHour",),
    "promo_end_hour": ("PromotionEndHour",),
    "promo_start_datetime": ("PromotionStartDateTime",),
    "promo_end_datetime": ("PromotionEndDateTime",),
    "reward_type": ("RewardType",),
    "discount_type": ("DiscountType",),
    "discount_rate": ("DiscountRate",),
    "discounted_price": ("DiscountedPrice", "DiscountedPricePerMida"),
    "min_qty": ("MinQty",),
    "max_qty": ("MaxQty",),
    "min_purchase_amount": ("MinPurchaseAmount", "MinPurchaseAmnt"),
    "min_items_offered": ("MinNoOfItemOffered", "MinNoOfItemOfered"),
    "club_id": ("ClubID", "ClubId"),
    "allow_stacking": ("AllowMultipleDiscounts",),
    "is_gift": ("IsGiftItem",),
    "additional_is_total": ("AdditionalIsTotal",),
    "additional_is_coupon": ("AdditionalIsCoupon",),
    "additional_gift_count": ("AdditionalGiftCount",),
}

# Repeating record tags, likewise per chain.
ITEM_TAGS = ("Item",)
PROMOTION_TAGS = ("Promotion",)
STORE_TAGS = ("Store",)
# Shufersal nests promotion items one level deeper and calls them PromotionItem.
PROMOTION_ITEM_TAGS = ("PromotionItem", "Item")

DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
)


def direct(element: etree._Element | None, key: str) -> str | None:
    """Read a field from an element's immediate children only."""
    if element is None:
        return None
    for tag in CANDIDATES[key]:
        found = element.find(tag)
        if found is not None and found.text and found.text.strip():
            return found.text.strip()
    return None


def deep(element: etree._Element | None, key: str) -> str | None:
    """Read a field from anywhere beneath an element.

    Needed because chains disagree about nesting depth: ClubId sits under
    AdditionalRestrictions/Clubs on Bina chains and directly on the promotion
    at Shufersal, and Shufersal keeps per-item discount fields under
    Groups/Group/PromotionItems/PromotionItem.
    """
    if element is None:
        return None
    for tag in CANDIDATES[key]:
        found = element.find(f".//{tag}")
        if found is not None and found.text and found.text.strip():
            return found.text.strip()
    return None


def to_decimal(raw: str | None) -> Decimal | None:
    """Parse a price. Empty, unparseable or non-positive becomes NULL, never 0.

    A zero price would silently poison every basket total (CLAUDE.md, sixth law).
    """
    if raw is None:
        return None
    text = raw.strip().replace(",", "").replace("₪", "")
    if not text:
        return None
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return value if value > 0 else None


def to_quantity(raw: str | None) -> Decimal | None:
    """Like to_decimal but zero is meaningful (QtyInPackage is often 0)."""
    if raw is None:
        return None
    text = raw.strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def to_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(float(raw.strip()))
    except (TypeError, ValueError):
        return None


def to_bool(raw: str | None) -> bool | None:
    if raw is None:
        return None
    return raw.strip() in {"1", "true", "True", "TRUE", "yes"}


def to_datetime(raw: str | None, time_part: str | None = None) -> datetime | None:
    """Parse a published timestamp into an aware UTC datetime.

    Stored as timestamptz in UTC and rendered in Asia/Jerusalem, so a naive
    published stamp is interpreted as local Israeli time -- which is what the
    retailers mean by it.
    """
    if not raw:
        return None
    text = raw.strip()
    if time_part and time_part.strip() and len(text) <= 10:
        text = f"{text} {time_part.strip()}"

    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=TZ).astimezone(timezone.utc)
    return None
