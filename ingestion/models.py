"""SQLAlchemy models for the ingestion layer.

Mirrors docs/03-DATA-MODEL.md, with three changes that Phase 0 forced:

  * `chains.gov_chain_id` is TEXT[], not TEXT. Three scrapers report several
    chain ids -- Victory and Mahsani Ashuk two each, Meshnat Yosef 2 three
    (PHASE0-FINDINGS F-7).
  * `ingestion_runs.status` has a `skipped_unstable` value. The library
    silently disables a chain it knows to be broken, which arrives as a
    successful run with zero files and is otherwise indistinguishable from a
    portal that just went down (F-5).
  * `stores.geom` and PostGIS are not here. Geocoding is a Phase 2 acceptance
    criterion, so Phase 1 runs on plain Postgres and the geography column
    arrives with the work that needs it.

Prices are NUMERIC, never float. A missing price is NULL, never 0 -- a zero
would silently poison every basket total.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Chain(Base):
    """A retail chain and how to reach its portal.

    Source addresses live here rather than in code: chains change portal, and
    a switch must be an UPDATE, not a deploy (ADR-006).
    """

    __tablename__ = "chains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name_he: Mapped[str] = mapped_column(Text, nullable=False)
    gov_chain_ids: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    scraper_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    scraper_fallback: Mapped[str | None] = mapped_column(Text)
    portal_type: Mapped[str] = mapped_column(Text, nullable=False)
    portal_url: Mapped[str | None] = mapped_column(Text)
    credentials_ref: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    price_groups: Mapped[list["PriceGroup"]] = relationship(back_populates="chain")
    stores: Mapped[list["Store"]] = relationship(back_populates="chain")


class PriceGroup(Base):
    """A set of stores that share prices.

    Phase 0 measurement #2 identified this as the published SubChain: Shufersal
    runs ten of them ("שופרסל שלי", "שופרסל דיל"...) across 420 stores, and
    prices vary by only 6.59% within a group. Keyed on the published id --
    Maayan2000 reports SubChainName as "1", so the name is not a reliable label.
    """

    __tablename__ = "price_groups"
    __table_args__ = (UniqueConstraint("chain_id", "sub_chain_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"), nullable=False)
    sub_chain_code: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text)

    chain: Mapped[Chain] = relationship(back_populates="price_groups")


class Store(Base):
    __tablename__ = "stores"
    __table_args__ = (UniqueConstraint("chain_id", "store_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"), nullable=False)
    price_group_id: Mapped[int | None] = mapped_column(ForeignKey("price_groups.id"))
    store_code: Mapped[str] = mapped_column(Text, nullable=False)
    name_he: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    # Shufersal publishes a numeric code here rather than a city name, so this
    # is kept raw and resolved during Phase 2 geocoding.
    city: Mapped[str | None] = mapped_column(Text)
    zip_code: Mapped[str | None] = mapped_column(Text)
    store_type: Mapped[str | None] = mapped_column(Text)
    # Filled by Phase 2 geocoding. Plain columns rather than a PostGIS
    # geography: at ~950 stores a haversine scan beats the index and removes a
    # deployment dependency (ADR-011).
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    geocode_confidence: Mapped[float | None] = mapped_column(Float)
    geocoded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The exact string sent to the provider, kept so a bad result can be
    # diagnosed without guessing what was asked.
    geocode_query: Mapped[str | None] = mapped_column(Text)
    # Set by a human. Overrides the confidence floor -- a verified store is
    # trusted for distance search whatever the provider thought.
    geocode_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    chain: Mapped[Chain] = relationship(back_populates="stores")


class IngestionRun(Base):
    """One chain's attempt at one ingestion cycle.

    Ingestion is best-effort: one chain failing must not stop the others, so
    every outcome is recorded here rather than raised.
    """

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chain_id: Mapped[int | None] = mapped_column(ForeignKey("chains.id"))
    scraper_name: Mapped[str | None] = mapped_column(Text)
    file_types: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    file_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_downloaded: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # ok | partial | failed | skipped_unstable | no_files
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


Index("idx_runs_chain_started", IngestionRun.chain_id, IngestionRun.started_at.desc())


class StagingItem(Base):
    """Raw normalised rows, before catalog matching.

    Deliberately denormalised and free of foreign keys: this table is written
    at high volume and truncated per run. Anything that needs a decision --
    which canonical product this is, whether it is trustworthy enough to
    compare -- happens downstream in Phase 2.
    """

    __tablename__ = "staging_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger)
    chain_id: Mapped[int | None] = mapped_column(Integer)
    sub_chain_code: Mapped[str | None] = mapped_column(Text)
    store_code: Mapped[str | None] = mapped_column(Text)
    item_code: Mapped[str | None] = mapped_column(Text)
    barcode: Mapped[str | None] = mapped_column(Text)
    item_type: Mapped[int | None] = mapped_column(SmallInteger)
    raw_name_he: Mapped[str | None] = mapped_column(Text)
    manufacturer: Mapped[str | None] = mapped_column(Text)
    unit_qty: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[float | None] = mapped_column(Numeric(12, 3))
    unit_of_measure: Mapped[str | None] = mapped_column(Text)
    is_weighted: Mapped[bool | None] = mapped_column(Boolean)
    price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    unit_price: Mapped[float | None] = mapped_column(Numeric(10, 4))
    price_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    file_date: Mapped[Date | None] = mapped_column(Date)
    source_key: Mapped[str | None] = mapped_column(String(512))


Index("idx_staging_run", StagingItem.run_id)
Index("idx_staging_barcode", StagingItem.barcode)
