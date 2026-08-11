"""Normalise published promotions and decide which ones v1 may apply.

Phase 0 measurement #3 found 99.72% of promotions arrive with their structured
fields populated and none as free text alone -- far better than the spec
feared. But populated is not the same as implementable. A promotion reading
"buy ice cream for 99 and get a cooler free" has every field filled and is a
threshold-plus-gift promotion, which the spec puts in v2.

So classification answers two separate questions:

    promo_kind      what shape is this?
    applicable_v1   may the basket engine act on it?

Anything the fields cannot prove is `unknown` and not applicable. It is still
stored, still counted, and still shown to the user as "3 further promotions in
this store were not included" -- which is the whole of ADR-008. A conservative
total with disclosure beats a confident wrong one, and user trust breaks once.

RewardType and DiscountType are deliberately not used to decide. Their meaning
varies between chains and no published mapping was verified in Phase 0;
classifying on them would be inventing precision.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from catalog.models import Promotion, PromotionItem
from ingestion.normalize import PromotionRow

log = logging.getLogger(__name__)

# Kinds the basket engine implements.
FIXED_PRICE = "fixed_price"
MIN_QTY = "min_qty"
PERCENT = "percent"
# Kinds it recognises but does not apply. Counted, never acted on.
THRESHOLD = "threshold"
GIFT = "gift"
UNKNOWN = "unknown"

V1_KINDS = frozenset({FIXED_PRICE, MIN_QTY, PERCENT})

# "0" is the published value for "all customers", i.e. not a club promotion.
NO_CLUB = {None, "", "0"}


@dataclass(frozen=True)
class Classification:
    kind: str
    applicable_v1: bool
    parse_status: str


def classify(row: PromotionRow) -> Classification:
    """Decide the shape of a promotion from the fields alone."""
    has_items = bool(row.item_codes)
    has_amount = row.discounted_price is not None
    has_rate = row.discount_rate is not None and row.discount_rate > 0
    min_qty = row.min_qty or Decimal(0)

    if not has_items:
        # Nothing to attach the discount to. Almost always a cross-category or
        # whole-basket promotion, which is v2 either way.
        return Classification(UNKNOWN, False, "partial")

    # Checked before the others: a threshold promotion often also carries a
    # per-item price, and applying that price without enforcing the threshold
    # would undercharge the basket.
    if row.min_purchase_amount and row.min_purchase_amount > 0:
        return Classification(THRESHOLD, False, "structured")

    if row.gift_item_codes:
        return Classification(GIFT, False, "structured")

    if has_amount and min_qty >= 2:
        return Classification(MIN_QTY, True, "structured")

    if has_amount:
        return Classification(FIXED_PRICE, True, "structured")

    if has_rate:
        return Classification(PERCENT, True, "structured")

    return Classification(UNKNOWN, False, "partial")


def is_club_only(row: PromotionRow) -> bool:
    """Club promotions are real but require membership, so the UI gates them."""
    return (row.club_id or "").strip() not in NO_CLUB


def store_promotions(
    session: Session,
    chain_id: int,
    store_id: int | None,
    rows: list[tuple[PromotionRow, list[int]]],
) -> dict[str, int]:
    """Persist promotions and their item links.

    `rows` pairs each promotion with the variant ids its item codes resolved
    to. Codes that resolved to nothing are dropped: a promotion cannot apply to
    a product the catalog does not know.
    """
    counts = {"stored": 0, "applicable": 0, "skipped": 0}

    for row, variant_ids in rows:
        verdict = classify(row)
        counts["stored"] += 1
        if verdict.applicable_v1:
            counts["applicable"] += 1
        else:
            counts["skipped"] += 1

        stmt = (
            pg_insert(Promotion)
            .values(
                chain_id=chain_id,
                store_id=store_id,
                promo_code=row.promo_code,
                description_he=row.description_he,
                promo_kind=verdict.kind,
                discount_type=row.discount_type,
                reward_type=row.reward_type,
                min_qty=row.min_qty,
                max_qty=row.max_qty,
                discount_rate=row.discount_rate,
                discounted_price=row.discounted_price,
                min_purchase_amount=row.min_purchase_amount,
                club_id=row.club_id,
                allow_stacking=row.allow_stacking,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
                parse_status=verdict.parse_status,
                applicable_v1=verdict.applicable_v1,
            )
            .returning(Promotion.id)
        )
        promotion_id = session.scalar(stmt)
        if promotion_id is None or not variant_ids:
            continue

        session.execute(
            pg_insert(PromotionItem)
            .values([{"promotion_id": promotion_id, "variant_id": vid} for vid in variant_ids])
            .on_conflict_do_nothing(index_elements=["promotion_id", "variant_id"])
        )

    return counts


def resolve_variants(session: Session, chain_id: int, item_codes: list[str]) -> list[int]:
    """Map a chain's item codes to variant ids, dropping anything unknown."""
    if not item_codes:
        return []
    from catalog.models import ProductVariant

    return list(
        session.scalars(
            select(ProductVariant.id).where(
                ProductVariant.chain_id == chain_id,
                ProductVariant.item_code.in_(item_codes),
            )
        )
    )
