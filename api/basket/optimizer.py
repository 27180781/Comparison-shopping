"""Where to buy the basket.

Deliberately not a solver. The problem is NP-hard in general and tiny in
practice -- at most ~25 stores in radius and ~40 items, so exhaustive k=1,
pairs over the top ten, and a conditional k=3 all finish in milliseconds
(ADR-005). An ILP would add a dependency, a build step and a debugging surface
in exchange for nothing measurable.

The interesting part is not the search, it is the assignment inside a pair.
Promotions break the independence between items -- "2 for 18" depends on how
many of that item land in the *same* store -- so items cannot simply be sent
to whichever store is cheaper for them individually. Greedy assignment
followed by local improvement gets very close for a negligible cost.

Travel cost is a user setting, never a magic number. The system's job is to
show the trade-off: "splitting across 2 stores saves 34 and adds 12 minutes."
The choice is the user's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from itertools import combinations

from api.basket.promotions import BasketLine, BasketTotal, PromotionOffer, apply_promotions

CENTS = Decimal("0.01")

# Pairs are drawn from the best single stores. Ten is comfortably past the
# point where a pair built from a worse store wins.
PAIR_POOL = 10


@dataclass(frozen=True)
class StoreOffer:
    """One store's prices and promotions for the requested basket."""

    store_id: int
    name: str
    chain_name: str
    distance_km: float | None
    travel_minutes: float | None
    # canonical_id -> (variant_id, unit_price)
    prices: dict[int, tuple[int, Decimal]]
    offers: list[PromotionOffer] = field(default_factory=list)
    skipped_promotions: int = 0

    def covers(self, canonical_id: int) -> bool:
        return canonical_id in self.prices


@dataclass(frozen=True)
class BasketRequest:
    """canonical_id -> quantity."""

    items: dict[int, int]
    travel_penalty_per_stop: Decimal = Decimal("0")
    travel_value_per_hour: Decimal = Decimal("0")
    include_club: bool = False


@dataclass
class Assignment:
    stores: list[StoreOffer]
    per_store: dict[int, BasketTotal]
    goods_total: Decimal
    travel_cost: Decimal
    missing: list[int]

    @property
    def total(self) -> Decimal:
        return (self.goods_total + self.travel_cost).quantize(CENTS)

    @property
    def applied_count(self) -> int:
        return sum(len(total.applied) for total in self.per_store.values())

    @property
    def skipped_count(self) -> int:
        return sum(total.skipped_count for total in self.per_store.values())


def _price_at(store: StoreOffer, request: BasketRequest, wanted: dict[int, int]) -> BasketTotal:
    lines = [
        BasketLine(
            variant_id=store.prices[canonical_id][0],
            qty=qty,
            unit_price=store.prices[canonical_id][1],
            canonical_id=canonical_id,
        )
        for canonical_id, qty in wanted.items()
        if store.covers(canonical_id)
    ]
    return apply_promotions(
        lines,
        store.offers,
        skipped_count=store.skipped_promotions,
        include_club=request.include_club,
    )


def _travel_cost(request: BasketRequest, stores: list[StoreOffer]) -> Decimal:
    """Cost of visiting these stores, in the user's own terms.

    Driving time rather than straight-line distance: in a dense metro the two
    differ enough to flip the answer.
    """
    if not request.travel_penalty_per_stop and not request.travel_value_per_hour:
        return Decimal("0.00")

    minutes = sum(Decimal(str(store.travel_minutes or 0)) for store in stores)
    stops = Decimal(len(stores))
    time_cost = request.travel_value_per_hour * minutes / Decimal(60)
    return (request.travel_penalty_per_stop * stops + time_cost).quantize(CENTS)


def solve_single(request: BasketRequest, stores: list[StoreOffer]) -> list[Assignment]:
    """Every store, priced for the whole basket. O(stores x items)."""
    results = []
    for store in stores:
        total = _price_at(store, request, request.items)
        missing = [cid for cid in request.items if not store.covers(cid)]
        results.append(
            Assignment(
                stores=[store],
                per_store={store.store_id: total},
                goods_total=total.total,
                travel_cost=_travel_cost(request, [store]),
                missing=missing,
            )
        )
    # Fewest missing items first: a cheap total that skips half the basket is
    # not a cheaper basket, and presenting it as one is the trust failure.
    results.sort(key=lambda a: (len(a.missing), a.total))
    return results


def assign_pair(
    request: BasketRequest, first: StoreOffer, second: StoreOffer
) -> tuple[dict[int, int], dict[int, int]]:
    """Split the basket between two stores: greedy, then improve until stable.

    Greedy alone is wrong whenever a promotion depends on quantity in one
    store, so each item is tested against a move to the other store and the
    move is kept only if the *whole* basket gets cheaper. Converges in two to
    four passes.
    """
    left: dict[int, int] = {}
    right: dict[int, int] = {}

    for canonical_id, qty in request.items.items():
        in_first = first.covers(canonical_id)
        in_second = second.covers(canonical_id)
        if in_first and not in_second:
            left[canonical_id] = qty
        elif in_second and not in_first:
            right[canonical_id] = qty
        elif in_first and in_second:
            if first.prices[canonical_id][1] <= second.prices[canonical_id][1]:
                left[canonical_id] = qty
            else:
                right[canonical_id] = qty

    def combined(a: dict[int, int], b: dict[int, int]) -> Decimal:
        return _price_at(first, request, a).total + _price_at(second, request, b).total

    best = combined(left, right)
    improved = True
    while improved:
        improved = False
        for canonical_id in list(request.items):
            if canonical_id in left and second.covers(canonical_id):
                trial_left = {k: v for k, v in left.items() if k != canonical_id}
                trial_right = {**right, canonical_id: left[canonical_id]}
            elif canonical_id in right and first.covers(canonical_id):
                trial_right = {k: v for k, v in right.items() if k != canonical_id}
                trial_left = {**left, canonical_id: right[canonical_id]}
            else:
                continue

            candidate = combined(trial_left, trial_right)
            if candidate < best:
                left, right, best = trial_left, trial_right, candidate
                improved = True

    return left, right


def solve_pairs(
    request: BasketRequest, stores: list[StoreOffer], pool: int = PAIR_POOL
) -> list[Assignment]:
    """Every pair drawn from the best single stores."""
    ranked = [assignment.stores[0] for assignment in solve_single(request, stores)][:pool]
    results = []

    for first, second in combinations(ranked, 2):
        left, right = assign_pair(request, first, second)
        left_total = _price_at(first, request, left)
        right_total = _price_at(second, request, right)
        covered = set(left) | set(right)
        results.append(
            Assignment(
                stores=[first, second],
                per_store={first.store_id: left_total, second.store_id: right_total},
                goods_total=(left_total.total + right_total.total).quantize(CENTS),
                travel_cost=_travel_cost(request, [first, second]),
                missing=[cid for cid in request.items if cid not in covered],
            )
        )

    results.sort(key=lambda a: (len(a.missing), a.total))
    return results


def optimize(
    request: BasketRequest, stores: list[StoreOffer], max_stores: int = 2
) -> dict[str, Assignment | None]:
    """Best single store, best pair, and the difference between them.

    Returning both rather than one winner is the point: the system shows the
    trade-off and the user decides whether the saving is worth the extra stop.
    """
    if not stores or not request.items:
        return {"single": None, "pair": None}

    singles = solve_single(request, stores)
    best_single = singles[0] if singles else None

    best_pair = None
    if max_stores >= 2 and len(stores) >= 2:
        pairs = solve_pairs(request, stores)
        if pairs:
            candidate = pairs[0]
            # A pair is only worth showing if it beats one store on coverage or
            # on money -- an equal split that adds a stop is strictly worse.
            better_coverage = best_single and len(candidate.missing) < len(best_single.missing)
            cheaper = best_single and candidate.total < best_single.total
            if better_coverage or cheaper:
                best_pair = candidate

    return {"single": best_single, "pair": best_pair}
