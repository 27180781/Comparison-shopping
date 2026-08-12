"""Request and response shapes.

Two things appear on every price the API returns, and neither is optional:
when it was last updated, and the reminder that the register wins. They are
here in the schema rather than left to the frontend so no client can ship a
price without them (ADR-010).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

PRICE_DISCLAIMER = "המחיר בקופה גובר. הנתונים באחריות הקמעונאים."


class StoreSummary(BaseModel):
    store_id: int
    name: str | None = None
    chain_name: str
    address: str | None = None
    city: str | None = None
    distance_km: float | None = None
    travel_minutes: float | None = None


class PriceAtStore(BaseModel):
    store: StoreSummary
    price: Decimal
    normalized_unit_price: Decimal | None = None
    unit_of_measure: str | None = None
    has_promotion: bool = False
    promotion_description: str | None = None
    updated_at: datetime


class ProductSummary(BaseModel):
    canonical_id: int
    barcode: str
    name_he: str
    brand: str | None = None
    pack_count: int = 1
    unit_size: Decimal | None = None
    unit_of_measure: str | None = None
    chain_count: int = 0
    image_url: str | None = None


class SearchResult(BaseModel):
    product: ProductSummary
    prices: list[PriceAtStore]
    cheapest_price: Decimal | None = None
    cheapest_unit_price: Decimal | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    total: int
    disclaimer: str = PRICE_DISCLAIMER
    # v1 compares packaged goods from national brands only. Saying so is not
    # marketing: chains with a strong private label look dearer than they are
    # for a real shopper (docs/01-SPEC.md §6).
    scope_note: str = "השוואת מוצרים ארוזים של מותגים מובילים"


class PricePoint(BaseModel):
    day: date
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    avg_price: Decimal | None = None
    store_count: int | None = None


class PriceHistoryResponse(BaseModel):
    product: ProductSummary
    days: int
    points: list[PricePoint]
    current_min: Decimal | None = None
    average: Decimal | None = None
    verdict: str | None = Field(
        default=None,
        description="נמוך מהרגיל / רגיל / גבוה מהרגיל, לפי ההיסטוריה",
    )
    disclaimer: str = PRICE_DISCLAIMER


class BasketItemRequest(BaseModel):
    canonical_id: int
    qty: int = Field(default=1, ge=1, le=99)


class BasketOptimizeRequest(BaseModel):
    items: list[BasketItemRequest] = Field(min_length=1, max_length=200)
    # Optional: "where is this basket cheapest" is a fair question without a
    # location, and the browser may refuse to give one. Without coordinates the
    # search covers every store carrying the products and travel cost is zero,
    # since there is no distance to price.
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float | None = Field(default=None, gt=0, le=100)
    # cheapest | balanced | single_store -- the travel trade-off is the user's
    # call, never a constant buried in the engine.
    mode: str = "balanced"
    include_club_promotions: bool = False
    max_stores: int = Field(default=2, ge=1, le=3)


class StoreBreakdown(BaseModel):
    store: StoreSummary
    items: int
    goods_total: Decimal
    saved: Decimal
    applied_promotions: int


class BasketPlan(BaseModel):
    stores: list[StoreBreakdown]
    goods_total: Decimal
    travel_cost: Decimal
    total: Decimal
    saved: Decimal
    applied_promotions: int
    # Required on every result: "this total includes 4 promotions; 3 further
    # promotions in these stores were not included."
    skipped_promotions: int
    missing_canonical_ids: list[int]


class BasketOptimizeResponse(BaseModel):
    single_store: BasketPlan | None = None
    split: BasketPlan | None = None
    split_saving: Decimal | None = None
    split_extra_minutes: float | None = None
    stores_considered: int
    missing_products: list[ProductSummary] = Field(default_factory=list)
    disclaimer: str = PRICE_DISCLAIMER
    scope_note: str = "השוואת מוצרים ארוזים של מותגים מובילים"


class ProductCard(BaseModel):
    """One product as the catalogue shows it: cheapest, dearest, and the gap.

    The gap is the point. A price is only interesting next to another price,
    and "the same product costs 5.41 here and 7.90 there" is the sentence the
    whole system exists to produce.
    """

    product: ProductSummary
    cheapest_price: Decimal | None = None
    dearest_price: Decimal | None = None
    spread: Decimal | None = None
    spread_pct: float | None = None
    cheapest_unit_price: Decimal | None = None
    cheapest_chain: str | None = None
    dearest_chain: str | None = None
    store_count: int = 0
    priced_chain_count: int = 0
    has_promotion: bool = False
    updated_at: datetime | None = None


class ProductBrowseResponse(BaseModel):
    results: list[ProductCard]
    total: int
    page: int
    page_size: int
    disclaimer: str = PRICE_DISCLAIMER
    scope_note: str = "השוואת מוצרים ארוזים של מותגים מובילים"


class FacetValue(BaseModel):
    value: str
    label: str
    count: int


class FiltersResponse(BaseModel):
    """The filter vocabulary, read from what is actually in the database.

    Never a hardcoded list: a chain with no priced rows must not be offered as
    a filter that silently returns nothing.
    """

    chains: list[FacetValue] = Field(default_factory=list)
    brands: list[FacetValue] = Field(default_factory=list)
    categories: list[FacetValue] = Field(default_factory=list)
    units: list[FacetValue] = Field(default_factory=list)
    price_min: Decimal | None = None
    price_max: Decimal | None = None


class ChainCoverage(BaseModel):
    chain_id: int
    name_he: str
    stores: int
    stores_geocoded: int
    variants: int
    priced_rows: int
    last_ingested: datetime | None = None


class CoverageResponse(BaseModel):
    """What the catalogue actually contains, per chain.

    Built for the interface rather than for operators: a shopper deserves to
    know which chains a comparison covered, and a chain that ingested prices
    but has no stores contributes nothing while looking healthy in a log.
    """

    chains: list[ChainCoverage]
    products: int
    products_multi_chain: int
    stores_total: int
    stores_geocoded: int
    prices_total: int
    last_updated: datetime | None = None


class HealthResponse(BaseModel):
    status: str
    database: bool
    chains_active: int | None = None
    products: int | None = None
    last_ingestion: datetime | None = None
    stale_chains: list[str] = Field(default_factory=list)
