"""Basket optimisation -- the core feature, built last because it needs the rest.

Loads every candidate store's prices and promotions for the requested products
in one pass, hands them to the optimizer, and reports both the best single
store and the best split so the user can weigh the saving against the extra
stop themselves.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import Float, and_, func, select
from sqlalchemy.orm import Session

from api import deps
from api.basket.optimizer import Assignment, BasketRequest, StoreOffer, optimize
from api.basket.promotions import PromotionOffer
from api.geo import bounding_box, estimate_travel_minutes, haversine_km
from api.schemas import (
    BasketOptimizeRequest,
    BasketOptimizeResponse,
    BasketPlan,
    ProductSummary,
    StoreBreakdown,
    StoreSummary,
)
from catalog.models import CanonicalProduct, PriceCurrent, Promotion, PromotionItem
from ingestion.models import Chain, Store

router = APIRouter(tags=["basket"])

# Beyond this the pair search stops being instant and the extra stores are
# further away than anyone will drive anyway.
MAX_CANDIDATE_STORES = 60


@router.post("/basket/optimize", response_model=BasketOptimizeResponse)
def optimize_basket(
    payload: BasketOptimizeRequest,
    session: Session = Depends(deps.get_session),
) -> BasketOptimizeResponse:
    radius = payload.radius_km or deps.default_radius_km()
    wanted = {item.canonical_id: item.qty for item in payload.items}

    stores = _candidate_stores(session, payload.lat, payload.lng, radius, wanted)
    penalty, hourly = deps.travel_settings(payload.mode)

    request = BasketRequest(
        items=wanted,
        travel_penalty_per_stop=penalty,
        travel_value_per_hour=hourly,
        include_club=payload.include_club_promotions,
    )
    max_stores = 1 if payload.mode == "single_store" else payload.max_stores
    solution = optimize(request, stores, max_stores=max_stores)

    single = _to_plan(solution["single"])
    split = _to_plan(solution["pair"])

    saving = None
    extra_minutes = None
    if single and split:
        saving = (single.total - split.total).quantize(Decimal("0.01"))
        extra_minutes = round(
            sum(entry.store.travel_minutes or 0 for entry in split.stores)
            - sum(entry.store.travel_minutes or 0 for entry in single.stores),
            1,
        )

    missing_ids = set(single.missing_canonical_ids if single else wanted)
    if split:
        missing_ids &= set(split.missing_canonical_ids)

    return BasketOptimizeResponse(
        single_store=single,
        split=split,
        split_saving=saving,
        split_extra_minutes=extra_minutes,
        stores_considered=len(stores),
        # Named explicitly rather than quietly dropped: a total that silently
        # omits items is a wrong answer presented as a right one.
        missing_products=_missing_products(session, sorted(missing_ids)),
    )


def _candidate_stores(
    session: Session,
    lat: float | None,
    lng: float | None,
    radius_km: float,
    wanted: dict[int, int],
) -> list[StoreOffer]:
    """Load prices and promotions for every candidate store, in two queries."""
    located = lat is not None and lng is not None
    # Without a location every store is a candidate and distance is unknown
    # rather than zero -- reporting 0.0 km would read as "next door".
    distance = (
        haversine_km(Store.lat, Store.lng, lat, lng) if located else func.cast(None, Float)
    )

    nearby = select(
        Store.id,
        Store.name_he,
        Store.address,
        Chain.name_he.label("chain_name"),
        distance.label("distance_km"),
    ).join(Chain, Chain.id == Store.chain_id).where(Store.is_active.is_(True))

    if located:
        nearby = nearby.where(
            bounding_box(Store.lat, Store.lng, lat, lng, radius_km),
            distance <= radius_km,
        ).order_by(distance)

    nearby = nearby.limit(MAX_CANDIDATE_STORES).subquery()

    rows = session.execute(
        select(
            nearby.c.id,
            nearby.c.name_he,
            nearby.c.chain_name,
            nearby.c.distance_km,
            PriceCurrent.canonical_id,
            PriceCurrent.variant_id,
            PriceCurrent.price,
        )
        .join(PriceCurrent, PriceCurrent.store_id == nearby.c.id)
        .where(
            PriceCurrent.canonical_id.in_(wanted.keys()),
            PriceCurrent.price.isnot(None),
        )
    ).all()

    by_store: dict[int, dict] = {}
    for store_id, name, chain_name, distance_km, canonical_id, variant_id, price in rows:
        entry = by_store.setdefault(
            store_id,
            {
                "name": name,
                "chain_name": chain_name,
                "distance_km": float(distance_km) if distance_km is not None else None,
                "prices": {},
            },
        )
        # A store can list the same product under several codes; keep the
        # cheapest, which is what a shopper would actually pay.
        existing = entry["prices"].get(canonical_id)
        if existing is None or price < existing[1]:
            entry["prices"][canonical_id] = (variant_id, price)

    variant_ids = {
        variant_id
        for entry in by_store.values()
        for variant_id, _ in entry["prices"].values()
    }
    offers_by_store = _offers(session, list(by_store), variant_ids)

    return [
        StoreOffer(
            store_id=store_id,
            name=entry["name"] or "",
            chain_name=entry["chain_name"],
            distance_km=(
                round(entry["distance_km"], 2) if entry["distance_km"] is not None else None
            ),
            travel_minutes=(
                estimate_travel_minutes(entry["distance_km"])
                if entry["distance_km"] is not None
                else None
            ),
            prices=entry["prices"],
            offers=offers_by_store.get(store_id, ([], 0))[0],
            skipped_promotions=offers_by_store.get(store_id, ([], 0))[1],
        )
        for store_id, entry in by_store.items()
    ]


def _offers(
    session: Session, store_ids: list[int], variant_ids: set[int]
) -> dict[int, tuple[list[PromotionOffer], int]]:
    """Active promotions per store, and how many were left out.

    Chain-wide promotions carry no store id and apply everywhere, so they are
    attached to every candidate store rather than dropped.
    """
    if not store_ids or not variant_ids:
        return {}

    now = func.now()
    rows = session.execute(
        select(
            Promotion.id,
            Promotion.store_id,
            Promotion.promo_kind,
            Promotion.min_qty,
            Promotion.discounted_price,
            Promotion.discount_rate,
            Promotion.allow_stacking,
            Promotion.club_id,
            Promotion.description_he,
            Promotion.applicable_v1,
            PromotionItem.variant_id,
        )
        .join(PromotionItem, PromotionItem.promotion_id == Promotion.id)
        .where(
            PromotionItem.variant_id.in_(variant_ids),
            (Promotion.store_id.in_(store_ids)) | (Promotion.store_id.is_(None)),
            and_(
                (Promotion.starts_at.is_(None)) | (Promotion.starts_at <= now),
                (Promotion.ends_at.is_(None)) | (Promotion.ends_at >= now),
            ),
        )
    ).all()

    grouped: dict[int, dict[int, PromotionOffer]] = {sid: {} for sid in store_ids}
    skipped: dict[int, set[int]] = {sid: set() for sid in store_ids}

    for row in rows:
        targets = [row.store_id] if row.store_id is not None else store_ids
        for store_id in targets:
            if store_id not in grouped:
                continue
            if not row.applicable_v1:
                # Counted so the UI can say how many were not included.
                skipped[store_id].add(row.id)
                continue

            existing = grouped[store_id].get(row.id)
            variants = (existing.variant_ids if existing else frozenset()) | {row.variant_id}
            grouped[store_id][row.id] = PromotionOffer(
                promotion_id=row.id,
                kind=row.promo_kind or "unknown",
                variant_ids=variants,
                min_qty=row.min_qty,
                discounted_price=row.discounted_price,
                discount_rate=row.discount_rate,
                allow_stacking=bool(row.allow_stacking),
                club_only=(row.club_id or "0").strip() not in {"", "0"},
                description=row.description_he,
            )

    return {
        store_id: (list(offers.values()), len(skipped[store_id]))
        for store_id, offers in grouped.items()
    }


def _to_plan(assignment: Assignment | None) -> BasketPlan | None:
    if assignment is None:
        return None

    breakdowns = []
    for store in assignment.stores:
        total = assignment.per_store[store.store_id]
        breakdowns.append(
            StoreBreakdown(
                store=StoreSummary(
                    store_id=store.store_id,
                    name=store.name,
                    chain_name=store.chain_name,
                    distance_km=store.distance_km,
                    travel_minutes=store.travel_minutes,
                ),
                items=len(total.applied) or sum(1 for _ in total.applied) or 0,
                goods_total=total.total,
                saved=total.saved,
                applied_promotions=len(total.applied),
            )
        )

    return BasketPlan(
        stores=breakdowns,
        goods_total=assignment.goods_total,
        travel_cost=assignment.travel_cost,
        total=assignment.total,
        saved=sum((total.saved for total in assignment.per_store.values()), Decimal("0.00")),
        applied_promotions=assignment.applied_count,
        skipped_promotions=assignment.skipped_count,
        missing_canonical_ids=assignment.missing,
    )


def _missing_products(session: Session, canonical_ids: list[int]) -> list[ProductSummary]:
    if not canonical_ids:
        return []
    products = session.scalars(
        select(CanonicalProduct).where(CanonicalProduct.id.in_(canonical_ids))
    )
    return [
        ProductSummary(
            canonical_id=product.id,
            barcode=product.barcode,
            name_he=product.name_he,
            brand=product.brand,
            pack_count=product.pack_count,
            unit_size=product.unit_size,
            unit_of_measure=product.unit_of_measure,
            chain_count=product.chain_count,
        )
        for product in products
    ]
