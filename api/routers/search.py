"""Single-product search -- the launch feature.

Simplest to build, easiest to explain, and the wedge that brings users in
before they will spend ten minutes assembling a basket (ADR-007).

The answer is a table: store, distance, drive time, price, price per unit,
promotion, last updated. Sorting by price per unit rather than sticker price is
the part that makes it honest, since the large pack is frequently the dearer
one and no shelf tells you that.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, and_, func, select
from sqlalchemy.orm import Session

from api import deps
from api.geo import bounding_box, estimate_travel_minutes, haversine_km
from api.schemas import (
    PriceAtStore,
    PriceHistoryResponse,
    PricePoint,
    ProductSummary,
    SearchResponse,
    SearchResult,
    StoreSummary,
)
from catalog.models import CanonicalProduct, PriceCurrent, PriceDaily, Promotion, PromotionItem
from ingestion.models import Chain, Store

router = APIRouter(tags=["search"])

HISTORY_DAYS = 90
# How far from the 90-day average counts as notable rather than noise.
NOTABLE_DELTA = Decimal("0.05")


def _product_summary(product: CanonicalProduct) -> ProductSummary:
    return ProductSummary(
        canonical_id=product.id,
        barcode=product.barcode,
        name_he=product.name_he,
        brand=product.brand,
        pack_count=product.pack_count,
        unit_size=product.unit_size,
        unit_of_measure=product.unit_of_measure,
        chain_count=product.chain_count,
        image_url=product.image_url,
    )


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(min_length=2, description="שם מוצר בעברית"),
    lat: float | None = Query(default=None, ge=-90, le=90),
    lng: float | None = Query(default=None, ge=-180, le=180),
    radius_km: float | None = Query(default=None, gt=0, le=100),
    sort: str = Query(default="unit_price", pattern="^(unit_price|price|distance)$"),
    session: Session = Depends(deps.get_session),
) -> SearchResponse:
    """Find products by Hebrew name and show where they are cheapest nearby."""
    radius = radius_km or deps.default_radius_km()
    threshold = deps.trigram_threshold()

    # word_similarity, not similarity: plain trigram similarity divides by the
    # length of both strings, so a short query against a long product name
    # scores near zero. "קוטג" against "קוטג' תנובה 5% 250 גרם" is exactly how
    # people search, and it has to match. word_similarity asks how well the
    # query matches some part of the name, which is the right question.
    # Argument order matters: needle first.
    score = func.word_similarity(q, CanonicalProduct.name_he)
    products = list(
        session.scalars(
            select(CanonicalProduct)
            .where(score > threshold)
            .order_by(score.desc(), CanonicalProduct.chain_count.desc())
            .limit(deps.search_result_limit())
        )
    )
    if not products:
        return SearchResponse(query=q, results=[], total=0)

    results = [
        SearchResult(
            product=_product_summary(product),
            prices=_prices_for(session, product.id, lat, lng, radius),
        )
        for product in products
    ]

    for result in results:
        if result.prices:
            result.cheapest_price = min(price.price for price in result.prices)
            unit_prices = [
                price.normalized_unit_price
                for price in result.prices
                if price.normalized_unit_price is not None
            ]
            result.cheapest_unit_price = min(unit_prices) if unit_prices else None

        if sort == "price":
            result.prices.sort(key=lambda p: p.price)
        elif sort == "distance":
            result.prices.sort(key=lambda p: (p.store.distance_km is None, p.store.distance_km))
        else:
            # Price per unit first, which is the comparison that actually holds.
            result.prices.sort(
                key=lambda p: (p.normalized_unit_price is None, p.normalized_unit_price, p.price)
            )

    return SearchResponse(query=q, results=results, total=len(results))


def _prices_for(
    session: Session,
    canonical_id: int,
    lat: float | None,
    lng: float | None,
    radius_km: float,
) -> list[PriceAtStore]:
    distance = (
        haversine_km(Store.lat, Store.lng, lat, lng)
        if lat is not None and lng is not None
        else func.cast(None, Float)
    )

    query = (
        select(
            PriceCurrent.price,
            PriceCurrent.normalized_unit_price,
            PriceCurrent.updated_at,
            PriceCurrent.variant_id,
            Store.id,
            Store.name_he,
            Store.address,
            Store.city,
            Chain.name_he,
            distance.label("distance_km"),
            CanonicalProduct.unit_of_measure,
        )
        .join(Store, Store.id == PriceCurrent.store_id)
        .join(Chain, Chain.id == Store.chain_id)
        .join(CanonicalProduct, CanonicalProduct.id == PriceCurrent.canonical_id)
        .where(PriceCurrent.canonical_id == canonical_id, Store.is_active.is_(True))
    )

    if lat is not None and lng is not None:
        query = query.where(bounding_box(Store.lat, Store.lng, lat, lng, radius_km))
        query = query.where(distance <= radius_km).order_by(distance)
    query = query.limit(deps.stores_per_product_limit())

    rows = session.execute(query).all()
    if not rows:
        return []

    promo_by_variant = _promotions_for(session, [row[3] for row in rows])

    return [
        PriceAtStore(
            store=StoreSummary(
                store_id=row[4],
                name=row[5],
                chain_name=row[8],
                address=row[6],
                city=row[7],
                distance_km=round(row[9], 2) if row[9] is not None else None,
                travel_minutes=estimate_travel_minutes(row[9]) if row[9] is not None else None,
            ),
            price=row[0],
            normalized_unit_price=row[1],
            unit_of_measure=row[10],
            has_promotion=row[3] in promo_by_variant,
            promotion_description=promo_by_variant.get(row[3]),
            # Shown on every price, without exception.
            updated_at=row[2],
        )
        for row in rows
    ]


def _promotions_for(session: Session, variant_ids: list[int]) -> dict[int, str]:
    """Currently valid promotions, by variant.

    Only promotions v1 can actually apply are surfaced as a promotion badge --
    flagging a threshold offer the engine will not honour invites the user to
    expect a discount they will not get.
    """
    if not variant_ids:
        return {}

    now = func.now()
    rows = session.execute(
        select(PromotionItem.variant_id, Promotion.description_he)
        .join(Promotion, Promotion.id == PromotionItem.promotion_id)
        .where(
            PromotionItem.variant_id.in_(variant_ids),
            Promotion.applicable_v1.is_(True),
            and_(
                (Promotion.starts_at.is_(None)) | (Promotion.starts_at <= now),
                (Promotion.ends_at.is_(None)) | (Promotion.ends_at >= now),
            ),
        )
    ).all()
    return {variant_id: description for variant_id, description in rows}


@router.get("/products/{canonical_id}/history", response_model=PriceHistoryResponse)
def price_history(
    canonical_id: int,
    days: int = Query(default=HISTORY_DAYS, ge=7, le=365),
    session: Session = Depends(deps.get_session),
) -> PriceHistoryResponse:
    """Price over time -- the question no shopping app answers.

    Not "is this cheap" but "was it ever anything else". A promotion that
    matches the price the product has held all year is not a promotion.
    """
    product = session.get(CanonicalProduct, canonical_id)
    if product is None:
        raise HTTPException(status_code=404, detail="מוצר לא נמצא")

    since = date.today() - timedelta(days=days)
    rows = session.execute(
        select(
            PriceDaily.day,
            PriceDaily.min_price,
            PriceDaily.max_price,
            PriceDaily.avg_price,
            PriceDaily.store_count,
        )
        .where(PriceDaily.canonical_id == canonical_id, PriceDaily.day >= since)
        .order_by(PriceDaily.day)
    ).all()

    points = [
        PricePoint(
            day=row[0], min_price=row[1], max_price=row[2], avg_price=row[3], store_count=row[4]
        )
        for row in rows
    ]

    current_min = session.scalar(
        select(func.min(PriceCurrent.price)).where(PriceCurrent.canonical_id == canonical_id)
    )
    averages = [point.avg_price for point in points if point.avg_price is not None]
    average = (sum(averages) / len(averages)) if averages else None

    verdict = None
    if current_min is not None and average:
        delta = (Decimal(current_min) - Decimal(average)) / Decimal(average)
        if delta <= -NOTABLE_DELTA:
            verdict = "נמוך מהרגיל"
        elif delta >= NOTABLE_DELTA:
            verdict = "גבוה מהרגיל"
        else:
            verdict = "רגיל"

    return PriceHistoryResponse(
        product=_product_summary(product),
        days=days,
        points=points,
        current_min=current_min,
        average=Decimal(average).quantize(Decimal("0.01")) if average else None,
        verdict=verdict,
    )
