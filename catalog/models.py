"""Catalog, price history and application models.

Follows docs/03-DATA-MODEL.md. Two deviations, both recorded in
docs/06-DECISIONS.md:

  * No PostGIS. Stores carry lat/lng and radius filtering is a haversine
    expression -- at ~950 stores nationwide a sequential scan beats the index
    overhead, and it removes a deployment dependency (ADR-011).
  * `price_groups.sub_chain_code` is the price group key, confirmed by Phase 0
    measurement #2: prices deviate 6.59% within a published SubChain, well
    under the 15% that would have sunk ADR-002.

Prices are NUMERIC throughout. History is SCD Type 2: a price change closes the
open row and opens a new one, so `price_base`/`price_exception` answer the
question nobody else can -- was this promotion ever actually a discount?
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ingestion.models import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name_he: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))


class CanonicalProduct(Base):
    """One real-world product, identified by its public barcode.

    Only products that clear CATALOG_MIN_CHAIN_COUNT get here. Phase 0
    measurement #4 found 62.7% of barcodes appear in exactly one chain -- those
    are private label or chain-specific items with no counterpart to compare
    against, so including them would mean comparing a product to nothing.
    """

    __tablename__ = "canonical_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    barcode: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name_he: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    pack_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    unit_size: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    unit_of_measure: Mapped[str | None] = mapped_column(Text)
    base_size: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3), Computed("pack_count * unit_size", persisted=True)
    )
    chain_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    image_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProductVariant(Base):
    """How one chain lists one product.

    `match_confidence` gates everything downstream: a variant that was not
    matched with confidence never enters a basket total silently. "Not found in
    X" beats comparing cottage cheese to soft white cheese (ADR-010).
    """

    __tablename__ = "product_variants"
    __table_args__ = (UniqueConstraint("chain_id", "item_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_id: Mapped[int | None] = mapped_column(ForeignKey("canonical_products.id"))
    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"), nullable=False)
    item_code: Mapped[str] = mapped_column(Text, nullable=False)
    barcode: Mapped[str | None] = mapped_column(Text)
    item_type: Mapped[int | None] = mapped_column(SmallInteger)
    raw_name_he: Mapped[str] = mapped_column(Text, nullable=False)
    is_weighted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_private_label: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    match_method: Mapped[str | None] = mapped_column(Text)
    match_confidence: Mapped[float | None] = mapped_column(Float)


class PriceBase(Base):
    """The price of a variant across a whole price group. SCD2."""

    __tablename__ = "price_base"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    price_group_id: Mapped[int] = mapped_column(ForeignKey("price_groups.id"), nullable=False)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PriceException(Base):
    """A single store disagreeing with its price group. SCD2.

    Phase 0 measured 6.59% of store prices landing here.
    """

    __tablename__ = "price_exception"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), nullable=False)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PriceCurrent(Base):
    """Materialised effective price per (store, variant). Rebuilt after ingestion.

    COALESCE(exception, base), flattened so the read path never joins history.
    """

    __tablename__ = "price_current"

    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), primary_key=True)
    canonical_id: Mapped[int | None] = mapped_column(ForeignKey("canonical_products.id"))
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # ILS per 100g / 100ml / unit. The comparison that matters, not raw price.
    normalized_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Promotion(Base):
    """A published promotion, normalised across chains.

    `parse_status` drives the transparency rule: the UI must say how many
    promotions were not included in a total. A conservative number with
    disclosure beats a confident wrong one (ADR-008).
    """

    __tablename__ = "promotions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"), nullable=False)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id"))
    promo_code: Mapped[str | None] = mapped_column(Text)
    description_he: Mapped[str | None] = mapped_column(Text)
    # fixed_price | min_qty | n_plus_m | percent | threshold | unknown
    promo_kind: Mapped[str | None] = mapped_column(Text)
    discount_type: Mapped[str | None] = mapped_column(Text)
    reward_type: Mapped[str | None] = mapped_column(Text)
    min_qty: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    max_qty: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    discount_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    discounted_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    min_purchase_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    club_id: Mapped[str | None] = mapped_column(Text)
    allow_stacking: Mapped[bool | None] = mapped_column(Boolean)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # structured | partial | text_only
    parse_status: Mapped[str | None] = mapped_column(Text)
    # False when the shape is known but v1 does not implement it (threshold,
    # gift, cross-category). Counted and surfaced, never silently applied.
    applicable_v1: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class PromotionItem(Base):
    __tablename__ = "promotion_items"

    promotion_id: Mapped[int] = mapped_column(
        ForeignKey("promotions.id", ondelete="CASCADE"), primary_key=True
    )
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("product_variants.id"), primary_key=True
    )
    is_gift: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserLocation(Base):
    __tablename__ = "user_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)


class Basket(Base):
    __tablename__ = "baskets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BasketItem(Base):
    __tablename__ = "basket_items"

    basket_id: Mapped[int] = mapped_column(
        ForeignKey("baskets.id", ondelete="CASCADE"), primary_key=True
    )
    canonical_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_products.id"), primary_key=True
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, server_default="1")


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    canonical_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_products.id"), nullable=False
    )
    threshold_type: Mapped[str | None] = mapped_column(Text)
    threshold_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PriceDaily(Base):
    """Daily min/max/avg per canonical product, for the 90-day chart.

    Kept separate from the SCD2 history so the chart is one indexed read rather
    than a range scan over every price change.
    """

    __tablename__ = "price_daily"

    canonical_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_products.id"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    max_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    avg_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    store_count: Mapped[int | None] = mapped_column(Integer)


Index("idx_pb_open", PriceBase.price_group_id, PriceBase.variant_id,
      postgresql_where=PriceBase.valid_to.is_(None))
Index("idx_pe_open", PriceException.store_id, PriceException.variant_id,
      postgresql_where=PriceException.valid_to.is_(None))
Index("idx_pc_canonical", PriceCurrent.canonical_id)
Index("idx_pv_canonical", ProductVariant.canonical_id)
Index("idx_pv_barcode", ProductVariant.barcode)
Index("idx_promo_active", Promotion.store_id, Promotion.starts_at, Promotion.ends_at)
Index("idx_promo_chain", Promotion.chain_id)
