"""Basket pricing and store selection, with hand-computed expected totals.

Every number here was worked out by hand first. That is the point of the suite:
the optimizer is allowed to be approximate about which stores to pick, but the
arithmetic on a given assignment has to be exactly right, because it is the
number a user acts on.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from api.basket.optimizer import BasketRequest, StoreOffer, assign_pair, optimize, solve_single
from api.basket.promotions import BasketLine, PromotionOffer, apply_promotions

COTTAGE, MILK, BREAD = 1, 2, 3


def offer(kind, variant_ids, **kwargs):
    return PromotionOffer(
        promotion_id=kwargs.pop("promotion_id", 1),
        kind=kind,
        variant_ids=frozenset(variant_ids),
        **kwargs,
    )


# ─── promotion arithmetic ────────────────────────────────────────────────────


def test_a_basket_without_promotions_is_just_the_sum():
    lines = [
        BasketLine(COTTAGE, 2, Decimal("6.00")),
        BasketLine(MILK, 1, Decimal("7.50")),
    ]
    result = apply_promotions(lines, [])
    assert result.total == Decimal("19.50")
    assert result.saved == Decimal("0.00")


def test_two_for_eighteen_on_three_units_charges_one_at_full_price():
    """The case that forbids summing first and discounting after.

    Three units under "2 for 18" is 18 + 9.50 = 27.50, not 28.50 minus a flat
    discount, and not 27.00.
    """
    lines = [BasketLine(COTTAGE, 3, Decimal("9.50"))]
    promo = offer("min_qty", [COTTAGE], min_qty=Decimal(2), discounted_price=Decimal("18.00"))

    result = apply_promotions(lines, [promo])
    assert result.total == Decimal("27.50")
    assert result.applied[0].saved == Decimal("1.00")


def test_min_qty_does_not_apply_below_its_threshold():
    lines = [BasketLine(COTTAGE, 1, Decimal("9.50"))]
    promo = offer("min_qty", [COTTAGE], min_qty=Decimal(2), discounted_price=Decimal("18.00"))

    result = apply_promotions(lines, [promo])
    assert result.total == Decimal("9.50")
    assert result.applied == []


def test_four_units_under_two_for_eighteen_is_two_bundles():
    lines = [BasketLine(COTTAGE, 4, Decimal("9.50"))]
    promo = offer("min_qty", [COTTAGE], min_qty=Decimal(2), discounted_price=Decimal("18.00"))
    assert apply_promotions(lines, [promo]).total == Decimal("36.00")


def test_fixed_price_replaces_the_shelf_price_per_unit():
    lines = [BasketLine(MILK, 3, Decimal("7.50"))]
    promo = offer("fixed_price", [MILK], discounted_price=Decimal("6.00"))
    assert apply_promotions(lines, [promo]).total == Decimal("18.00")


@pytest.mark.parametrize("rate", [Decimal("20"), Decimal("0.20")])
def test_percentage_is_read_whether_published_as_20_or_as_0_20(rate):
    lines = [BasketLine(BREAD, 2, Decimal("10.00"))]
    promo = offer("percent", [BREAD], discount_rate=rate)
    assert apply_promotions(lines, [promo]).total == Decimal("16.00")


def test_the_best_offer_wins_when_several_apply():
    lines = [BasketLine(COTTAGE, 2, Decimal("10.00"))]
    weak = offer("fixed_price", [COTTAGE], discounted_price=Decimal("9.00"), promotion_id=1)
    strong = offer("min_qty", [COTTAGE], min_qty=Decimal(2),
                   discounted_price=Decimal("15.00"), promotion_id=2)

    result = apply_promotions(lines, [weak, strong])
    assert result.total == Decimal("15.00")
    assert result.applied[0].promotion_id == 2


def test_a_promotion_never_makes_a_line_more_expensive():
    """A published 'discount' above the shelf price must be ignored."""
    lines = [BasketLine(MILK, 1, Decimal("5.00"))]
    promo = offer("fixed_price", [MILK], discounted_price=Decimal("8.00"))
    assert apply_promotions(lines, [promo]).total == Decimal("5.00")


def test_inapplicable_kinds_are_counted_not_applied():
    """Threshold and gift promotions are v2; silently applying one is the bug."""
    lines = [BasketLine(MILK, 1, Decimal("7.50"))]
    threshold = offer("threshold", [MILK], discounted_price=Decimal("1.00"))

    result = apply_promotions(lines, [threshold], skipped_count=3)
    assert result.total == Decimal("7.50")
    assert result.skipped_count == 3


def test_club_promotions_are_excluded_unless_the_user_opts_in():
    lines = [BasketLine(MILK, 2, Decimal("7.00"))]
    club = offer("fixed_price", [MILK], discounted_price=Decimal("5.00"), club_only=True)

    without = apply_promotions(lines, [club], include_club=False)
    assert without.total == Decimal("14.00")
    assert without.skipped_count == 1

    with_club = apply_promotions(lines, [club], include_club=True)
    assert with_club.total == Decimal("10.00")


# ─── store selection ─────────────────────────────────────────────────────────


def store(store_id, prices, offers=(), distance=1.0, minutes=5.0, skipped=0):
    return StoreOffer(
        store_id=store_id,
        name=f"store-{store_id}",
        chain_name="chain",
        distance_km=distance,
        travel_minutes=minutes,
        prices={cid: (cid, Decimal(price)) for cid, price in prices.items()},
        offers=list(offers),
        skipped_promotions=skipped,
    )


def test_cheapest_single_store_wins():
    request = BasketRequest(items={COTTAGE: 1, MILK: 1})
    stores = [
        store(1, {COTTAGE: "6.00", MILK: "8.00"}),
        store(2, {COTTAGE: "5.00", MILK: "7.00"}),
    ]
    best = solve_single(request, stores)[0]
    assert best.stores[0].store_id == 2
    assert best.goods_total == Decimal("12.00")


def test_a_store_missing_half_the_basket_does_not_win_on_price():
    """A cheap total that skips items is not a cheaper basket."""
    request = BasketRequest(items={COTTAGE: 1, MILK: 1, BREAD: 1})
    stores = [
        store(1, {COTTAGE: "1.00"}),  # cheap but carries one item
        store(2, {COTTAGE: "6.00", MILK: "8.00", BREAD: "5.00"}),
    ]
    best = solve_single(request, stores)[0]
    assert best.stores[0].store_id == 2
    assert best.missing == []


def test_missing_items_are_reported_explicitly():
    request = BasketRequest(items={COTTAGE: 1, MILK: 1})
    best = solve_single(request, [store(1, {COTTAGE: "6.00"})])[0]
    assert best.missing == [MILK]


def test_pair_assignment_keeps_a_quantity_promotion_intact():
    """The reason greedy assignment alone is wrong.

    Store 1 is cheaper per unit for cottage, so greedy sends both units there.
    But store 2 runs "2 for 10", which beats 2 x 5.50. Local improvement has to
    find that by moving the whole line rather than each unit.
    """
    request = BasketRequest(items={COTTAGE: 2, MILK: 1})
    first = store(1, {COTTAGE: "5.50", MILK: "4.00"})
    second = store(
        2,
        {COTTAGE: "6.00", MILK: "9.00"},
        offers=[offer("min_qty", [COTTAGE], min_qty=Decimal(2),
                      discounted_price=Decimal("10.00"))],
    )

    left, right = assign_pair(request, first, second)
    assert right.get(COTTAGE) == 2, "the quantity promotion must not be split"
    assert left.get(MILK) == 1


def test_splitting_is_only_offered_when_it_actually_wins():
    request = BasketRequest(items={COTTAGE: 1, MILK: 1})
    stores = [
        store(1, {COTTAGE: "5.00", MILK: "7.00"}),
        store(2, {COTTAGE: "5.10", MILK: "7.10"}),
    ]
    result = optimize(request, stores)
    assert result["single"].goods_total == Decimal("12.00")
    assert result["pair"] is None


def test_splitting_is_offered_when_it_saves_money():
    request = BasketRequest(items={COTTAGE: 1, MILK: 1})
    stores = [
        store(1, {COTTAGE: "5.00", MILK: "20.00"}),
        store(2, {COTTAGE: "20.00", MILK: "6.00"}),
    ]
    result = optimize(request, stores)
    assert result["single"].goods_total == Decimal("25.00")
    assert result["pair"].goods_total == Decimal("11.00")


def test_travel_cost_can_make_a_split_not_worth_it():
    """The trade-off the user chooses, not one the system decides for them."""
    items = {COTTAGE: 1, MILK: 1}
    stores = [
        store(1, {COTTAGE: "5.00", MILK: "12.00"}, minutes=5),
        store(2, {COTTAGE: "20.00", MILK: "6.00"}, minutes=30),
    ]

    cheapest = optimize(BasketRequest(items=items), stores)
    assert cheapest["pair"] is not None

    balanced = optimize(
        BasketRequest(
            items=items,
            travel_penalty_per_stop=Decimal("15"),
            travel_value_per_hour=Decimal("40"),
        ),
        stores,
    )
    assert balanced["pair"] is None


def test_skipped_promotions_are_carried_up_to_the_result():
    """The count the UI is required to show."""
    request = BasketRequest(items={COTTAGE: 1})
    best = solve_single(request, [store(1, {COTTAGE: "6.00"}, skipped=4)])[0]
    assert best.skipped_count == 4


def test_empty_basket_and_no_stores_are_handled():
    assert optimize(BasketRequest(items={}), []) == {"single": None, "pair": None}
    assert optimize(BasketRequest(items={COTTAGE: 1}), []) == {"single": None, "pair": None}


def test_a_forty_item_basket_across_twenty_five_stores_stays_fast():
    """The scale ADR-005 is based on. Must be milliseconds, not a solver."""
    import time

    items = {cid: 1 + cid % 3 for cid in range(40)}
    stores = [
        store(sid, {cid: f"{5 + (cid * sid) % 17}.50" for cid in range(40)})
        for sid in range(1, 26)
    ]

    started = time.perf_counter()
    result = optimize(BasketRequest(items=items), stores)
    elapsed = time.perf_counter() - started

    assert result["single"] is not None
    assert elapsed < 0.5, f"took {elapsed:.3f}s"
