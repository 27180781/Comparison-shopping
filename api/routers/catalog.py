"""Browsing the catalogue -- the database made visible.

Search answers "what does X cost". This answers the question that comes before
it: "what is in here, and where is the gap widest". Every filter is read from
what the database actually holds, so a chain with no priced rows is never
offered as an option that silently returns nothing.

The headline number on a card is the spread, not the price. A price alone says
nothing; the same cottage cheese at 5.41 in one chain and 7.90 in another is
the entire point of the system, and sorting by that gap puts the products
worth switching stores for at the top.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, and_, distinct, func, select
from sqlalchemy.orm import Session

from api import deps
from api.geo import bounding_box, haversine_km
from api.schemas import (
    FacetValue,
    FiltersResponse,
    ProductBrowseResponse,
    ProductCard,
    ProductSummary,
)
from catalog.models import (
    CanonicalProduct,
    Category,
    PriceCurrent,
    Promotion,
    PromotionItem,
    ProductVariant,
)
from ingestion.models import Chain, Store

router = APIRouter(tags=["catalog"])

MAX_PAGE_SIZE = 60
# Enough to fill a filter menu without turning it into a directory.
MAX_FACET_VALUES = 40

SORTS = ("spread", "price", "unit_price", "name", "chains", "updated")


@router.get("/products", response_model=ProductBrowseResponse)
def browse(
    q: str | None = Query(default=None, min_length=2),
    chain_ids: list[int] | None = Query(default=None),
    brands: list[str] | None = Query(default=None),
    category_id: int | None = Query(default=None),
    unit: str | None = Query(default=None, pattern="^(g|ml|unit)$"),
    min_price: Decimal | None = Query(default=None, ge=0),
    max_price: Decimal | None = Query(default=None, ge=0),
    min_chains: int = Query(default=1, ge=1, le=13),
    promo_only: bool = Query(default=False),
    lat: float | None = Query(default=None, ge=-90, le=90),
    lng: float | None = Query(default=None, ge=-180, le=180),
    radius_km: float | None = Query(default=None, gt=0, le=100),
    sort: str = Query(default="spread"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=MAX_PAGE_SIZE),
    session: Session = Depends(deps.get_session),
) -> ProductBrowseResponse:
    """Filter the catalogue and rank it by how much switching stores is worth."""
    if sort not in SORTS:
        sort = "spread"

    agg = _price_aggregate(chain_ids, lat, lng, radius_km or deps.default_radius_km()).subquery()

    spread = (agg.c.dearest - agg.c.cheapest).label("spread")
    query = select(CanonicalProduct, agg, spread).join(agg, agg.c.cid == CanonicalProduct.id)

    if q:
        query = query.where(func.word_similarity(q, CanonicalProduct.name_he) > deps.trigram_threshold())
    if brands:
        query = query.where(CanonicalProduct.brand.in_(brands))
    if category_id is not None:
        query = query.where(CanonicalProduct.category_id == category_id)
    if unit:
        query = query.where(CanonicalProduct.unit_of_measure == unit)
    if min_price is not None:
        query = query.where(agg.c.cheapest >= min_price)
    if max_price is not None:
        query = query.where(agg.c.cheapest <= max_price)
    if min_chains > 1:
        # Counted from priced rows rather than the catalogue column: a product
        # listed by three chains but currently priced by one is comparable to
        # nothing today, whatever the catalogue says.
        query = query.where(agg.c.chains >= min_chains)
    if promo_only:
        query = query.where(_has_promotion_clause())

    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0

    orderings = {
        # Widest gap first: the products where choosing the right store pays.
        "spread": (spread.desc().nullslast(), agg.c.cheapest.asc()),
        "price": (agg.c.cheapest.asc().nullslast(),),
        "unit_price": (agg.c.unit_price.asc().nullslast(), agg.c.cheapest.asc()),
        "name": (CanonicalProduct.name_he.asc(),),
        "chains": (agg.c.chains.desc(), spread.desc().nullslast()),
        "updated": (agg.c.updated.desc().nullslast(),),
    }
    query = query.order_by(*orderings[sort], CanonicalProduct.id.asc())
    rows = session.execute(query.offset((page - 1) * page_size).limit(page_size)).all()

    canonical_ids = [row[0].id for row in rows]
    extremes = _cheapest_and_dearest_chain(session, canonical_ids, chain_ids)
    promoted = _promoted(session, canonical_ids)

    results = []
    for row in rows:
        product = row[0]
        cheapest, dearest = row.cheapest, row.dearest
        gap = (dearest - cheapest) if (cheapest is not None and dearest is not None) else None
        low, high = extremes.get(product.id, (None, None))
        results.append(
            ProductCard(
                product=_summary(product),
                cheapest_price=cheapest,
                dearest_price=dearest,
                spread=gap,
                spread_pct=(
                    round(float(gap / dearest) * 100, 1) if gap is not None and dearest else None
                ),
                cheapest_unit_price=row.unit_price,
                cheapest_chain=low,
                dearest_chain=high,
                store_count=row.stores or 0,
                priced_chain_count=row.chains or 0,
                has_promotion=product.id in promoted,
                updated_at=row.updated,
            )
        )

    return ProductBrowseResponse(results=results, total=total, page=page, page_size=page_size)


@router.get("/filters", response_model=FiltersResponse)
def filters(session: Session = Depends(deps.get_session)) -> FiltersResponse:
    """The filter vocabulary, counted from priced rows."""
    priced = (
        select(
            PriceCurrent.canonical_id.label("cid"),
            Store.chain_id.label("chain_id"),
        )
        .join(Store, Store.id == PriceCurrent.store_id)
        .where(PriceCurrent.canonical_id.isnot(None), PriceCurrent.price.isnot(None))
        .distinct()
        .subquery()
    )

    chains = [
        FacetValue(value=str(row[0]), label=row[1], count=row[2])
        for row in session.execute(
            select(Chain.id, Chain.name_he, func.count(distinct(priced.c.cid)))
            .join(priced, priced.c.chain_id == Chain.id)
            .group_by(Chain.id, Chain.name_he)
            .order_by(func.count(distinct(priced.c.cid)).desc())
        ).all()
    ]

    comparable = select(distinct(priced.c.cid)).scalar_subquery()

    brands = [
        FacetValue(value=row[0], label=row[0], count=row[1])
        for row in session.execute(
            select(CanonicalProduct.brand, func.count())
            .where(CanonicalProduct.brand.isnot(None), CanonicalProduct.id.in_(comparable))
            .group_by(CanonicalProduct.brand)
            .order_by(func.count().desc())
            .limit(MAX_FACET_VALUES)
        ).all()
    ]

    categories = [
        FacetValue(value=str(row[0]), label=row[1], count=row[2])
        for row in session.execute(
            select(Category.id, Category.name_he, func.count())
            .join(CanonicalProduct, CanonicalProduct.category_id == Category.id)
            .where(CanonicalProduct.id.in_(comparable))
            .group_by(Category.id, Category.name_he)
            .order_by(func.count().desc())
            .limit(MAX_FACET_VALUES)
        ).all()
    ]

    unit_labels = {"g": "לפי משקל", "ml": "לפי נפח", "unit": "לפי יחידה"}
    units = [
        FacetValue(value=row[0], label=unit_labels.get(row[0], row[0]), count=row[1])
        for row in session.execute(
            select(CanonicalProduct.unit_of_measure, func.count())
            .where(
                CanonicalProduct.unit_of_measure.isnot(None),
                CanonicalProduct.id.in_(comparable),
            )
            .group_by(CanonicalProduct.unit_of_measure)
            .order_by(func.count().desc())
        ).all()
    ]

    bounds = session.execute(
        select(func.min(PriceCurrent.price), func.max(PriceCurrent.price)).where(
            PriceCurrent.canonical_id.isnot(None)
        )
    ).first()

    return FiltersResponse(
        chains=chains,
        brands=brands,
        categories=categories,
        units=units,
        price_min=bounds[0] if bounds else None,
        price_max=bounds[1] if bounds else None,
    )


def _price_aggregate(
    chain_ids: list[int] | None,
    lat: float | None,
    lng: float | None,
    radius_km: float,
) -> Select:
    """Cheapest, dearest and reach per product, over the stores in scope."""
    query = (
        select(
            PriceCurrent.canonical_id.label("cid"),
            func.min(PriceCurrent.price).label("cheapest"),
            func.max(PriceCurrent.price).label("dearest"),
            func.min(PriceCurrent.normalized_unit_price).label("unit_price"),
            func.count(distinct(PriceCurrent.store_id)).label("stores"),
            func.count(distinct(Store.chain_id)).label("chains"),
            func.max(PriceCurrent.updated_at).label("updated"),
        )
        .join(Store, Store.id == PriceCurrent.store_id)
        .where(
            PriceCurrent.canonical_id.isnot(None),
            PriceCurrent.price.isnot(None),
            Store.is_active.is_(True),
        )
        .group_by(PriceCurrent.canonical_id)
    )

    if chain_ids:
        query = query.where(Store.chain_id.in_(chain_ids))
    if lat is not None and lng is not None:
        query = query.where(
            bounding_box(Store.lat, Store.lng, lat, lng, radius_km),
            haversine_km(Store.lat, Store.lng, lat, lng) <= radius_km,
        )
    return query


def _has_promotion_clause():
    now = func.now()
    return (
        select(PromotionItem.promotion_id)
        .join(Promotion, Promotion.id == PromotionItem.promotion_id)
        .join(ProductVariant, ProductVariant.id == PromotionItem.variant_id)
        .where(
            ProductVariant.canonical_id == CanonicalProduct.id,
            Promotion.applicable_v1.is_(True),
            and_(
                (Promotion.starts_at.is_(None)) | (Promotion.starts_at <= now),
                (Promotion.ends_at.is_(None)) | (Promotion.ends_at >= now),
            ),
        )
        .exists()
    )


def _promoted(session: Session, canonical_ids: list[int]) -> set[int]:
    if not canonical_ids:
        return set()
    now = func.now()
    return set(
        session.scalars(
            select(distinct(ProductVariant.canonical_id))
            .join(PromotionItem, PromotionItem.variant_id == ProductVariant.id)
            .join(Promotion, Promotion.id == PromotionItem.promotion_id)
            .where(
                ProductVariant.canonical_id.in_(canonical_ids),
                Promotion.applicable_v1.is_(True),
                and_(
                    (Promotion.starts_at.is_(None)) | (Promotion.starts_at <= now),
                    (Promotion.ends_at.is_(None)) | (Promotion.ends_at >= now),
                ),
            )
        )
    )


def _cheapest_and_dearest_chain(
    session: Session, canonical_ids: list[int], chain_ids: list[int] | None
) -> dict[int, tuple[str | None, str | None]]:
    """Name the chain at each end of the range.

    "5.41 in Rami Levy, 7.90 in Shufersal" is actionable; a bare range is not.
    """
    if not canonical_ids:
        return {}

    query = (
        select(PriceCurrent.canonical_id, PriceCurrent.price, Chain.name_he)
        .join(Store, Store.id == PriceCurrent.store_id)
        .join(Chain, Chain.id == Store.chain_id)
        .where(
            PriceCurrent.canonical_id.in_(canonical_ids),
            PriceCurrent.price.isnot(None),
            Store.is_active.is_(True),
        )
    )
    if chain_ids:
        query = query.where(Store.chain_id.in_(chain_ids))

    extremes: dict[int, tuple[str | None, str | None]] = {}
    best: dict[int, Decimal] = {}
    worst: dict[int, Decimal] = {}
    for canonical_id, price, chain_name in session.execute(query).all():
        low_name, high_name = extremes.get(canonical_id, (None, None))
        if canonical_id not in best or price < best[canonical_id]:
            best[canonical_id] = price
            low_name = chain_name
        if canonical_id not in worst or price > worst[canonical_id]:
            worst[canonical_id] = price
            high_name = chain_name
        extremes[canonical_id] = (low_name, high_name)
    return extremes


def _summary(product: CanonicalProduct) -> ProductSummary:
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
