"""Evaluate promotions against a basket.

The rule that shapes everything here: never sum the line prices and then
subtract discounts. Each promotion is evaluated against the actual quantity of
the items it applies to, because "2 for 18" on three units is 18 + one at full
price, not 27 minus something.

Promotions the catalog marked inapplicable are counted, never applied. The
caller is expected to surface that count -- "this total includes 4 promotions;
3 further promotions in this store were not included" is not a nicety, it is
the difference between a number a user can trust and one they cannot (ADR-008).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

CENTS = Decimal("0.01")


@dataclass(frozen=True)
class BasketLine:
    variant_id: int
    qty: int
    unit_price: Decimal
    canonical_id: int | None = None


@dataclass(frozen=True)
class PromotionOffer:
    """A promotion, already narrowed to the variants it covers in this store."""

    promotion_id: int
    kind: str
    variant_ids: frozenset[int]
    min_qty: Decimal | None = None
    discounted_price: Decimal | None = None
    discount_rate: Decimal | None = None
    allow_stacking: bool = False
    club_only: bool = False
    description: str | None = None


@dataclass
class AppliedPromotion:
    promotion_id: int
    kind: str
    variant_id: int
    units: int
    saved: Decimal
    description: str | None = None


@dataclass
class BasketTotal:
    total: Decimal = Decimal("0.00")
    undiscounted_total: Decimal = Decimal("0.00")
    applied: list[AppliedPromotion] = field(default_factory=list)
    skipped_count: int = 0

    @property
    def saved(self) -> Decimal:
        return (self.undiscounted_total - self.total).quantize(CENTS)


def _line_cost_under(offer: PromotionOffer, line: BasketLine) -> Decimal | None:
    """What this line costs under one promotion, or None if it does not apply."""
    if line.variant_id not in offer.variant_ids or line.qty <= 0:
        return None

    if offer.kind == "fixed_price" and offer.discounted_price is not None:
        return offer.discounted_price * line.qty

    if offer.kind == "min_qty" and offer.discounted_price is not None:
        threshold = int(offer.min_qty or 0)
        if threshold < 2 or line.qty < threshold:
            return None
        # "2 for 18" on 5 units is two bundles at 18 plus one at full price.
        bundles, remainder = divmod(line.qty, threshold)
        return offer.discounted_price * bundles + line.unit_price * remainder

    if offer.kind == "percent" and offer.discount_rate:
        rate = offer.discount_rate
        # Chains publish this both as 15 and as 0.15.
        fraction = rate / 100 if rate > 1 else rate
        return line.unit_price * line.qty * (Decimal(1) - fraction)

    return None


def apply_promotions(
    lines: list[BasketLine],
    offers: list[PromotionOffer],
    skipped_count: int = 0,
    include_club: bool = False,
) -> BasketTotal:
    """Price a basket, applying the best offer available to each line.

    Only one promotion is applied per line even where a chain permits stacking.
    Stacking rules are published inconsistently and getting them wrong
    understates the total, which is the failure direction that loses trust --
    so the conservative reading is deliberate and the count of unapplied
    promotions is reported alongside.
    """
    result = BasketTotal()
    usable = [
        offer
        for offer in offers
        if offer.kind in {"fixed_price", "min_qty", "percent"}
        and (include_club or not offer.club_only)
    ]
    result.skipped_count = skipped_count + sum(
        1 for offer in offers if offer.club_only and not include_club
    )

    for line in lines:
        full_price = (line.unit_price * line.qty).quantize(CENTS)
        result.undiscounted_total += full_price

        best_cost = full_price
        best_offer: PromotionOffer | None = None
        for offer in usable:
            cost = _line_cost_under(offer, line)
            if cost is None:
                continue
            cost = cost.quantize(CENTS)
            if cost < best_cost:
                best_cost, best_offer = cost, offer

        result.total += best_cost
        if best_offer is not None:
            result.applied.append(
                AppliedPromotion(
                    promotion_id=best_offer.promotion_id,
                    kind=best_offer.kind,
                    variant_id=line.variant_id,
                    units=line.qty,
                    saved=(full_price - best_cost).quantize(CENTS),
                    description=best_offer.description,
                )
            )

    result.total = result.total.quantize(CENTS)
    result.undiscounted_total = result.undiscounted_total.quantize(CENTS)
    return result
