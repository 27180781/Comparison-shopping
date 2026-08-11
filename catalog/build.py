"""Turn staged rows into a catalog worth comparing.

Three steps, in order:

  1. Every staged row becomes a `product_variant` -- how one chain lists one
     item. Cheap, lossless, no judgement.
  2. Barcodes seen in at least CATALOG_MIN_CHAIN_COUNT chains become
     `canonical_products`. Phase 0 measurement #4 found 62.7% of barcodes
     appear in exactly one chain: private label and chain-specific items with
     no counterpart, so comparing them would mean comparing a product to
     nothing.
  3. Variants are linked to their canonical product and given a match
     confidence.

The confidence gate is the point. A variant that is not confidently matched is
left unlinked, which means it never enters a basket total silently. "Not found
in X" beats comparing cottage cheese to soft white cheese (ADR-010).

Barcode matching is exact by construction: two chains listing the same GTIN are
listing the same product. So confidence is 1.0 here, and the fuzzier routes --
text signatures, embeddings, equivalence classes for private label -- are v2,
which is why match_method exists at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import Integer, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from catalog.models import CanonicalProduct, ProductVariant
from catalog.packsize import parse_pack
from ingestion.config import settings
from ingestion.models import StagingItem

log = logging.getLogger(__name__)

MATCH_BARCODE_EXACT = "barcode"
BARCODE_CONFIDENCE = 1.0


@dataclass
class BuildReport:
    variants_upserted: int = 0
    barcodes_seen: int = 0
    canonicals_created: int = 0
    canonicals_updated: int = 0
    variants_linked: int = 0
    variants_unlinked: int = 0

    def as_dict(self) -> dict[str, int]:
        return self.__dict__.copy()


def min_chain_count() -> int:
    """How many chains must carry a barcode before it is worth comparing."""
    from ingestion.config import _int  # noqa: PLC0415 - keeps all tunables in one place

    return _int("CATALOG_MIN_CHAIN_COUNT", 2)


def build(session: Session) -> BuildReport:
    report = BuildReport()
    report.variants_upserted = _upsert_variants(session)
    report.barcodes_seen, created, updated = _upsert_canonicals(session)
    report.canonicals_created = created
    report.canonicals_updated = updated
    report.variants_linked, report.variants_unlinked = _link_variants(session)
    _refresh_chain_counts(session)
    return report


def _upsert_variants(session: Session) -> int:
    """One row per (chain, item_code), carrying the chain's own name for it.

    Names change between publications, so the latest wins -- but only when a
    real name is present, since a blank must not erase a good one.
    """
    latest = (
        select(
            StagingItem.chain_id,
            StagingItem.item_code,
            func.max(StagingItem.barcode).label("barcode"),
            func.max(StagingItem.item_type).label("item_type"),
            func.max(StagingItem.raw_name_he).label("raw_name_he"),
            func.bool_or(StagingItem.is_weighted).label("is_weighted"),
        )
        .where(StagingItem.item_code.isnot(None), StagingItem.raw_name_he.isnot(None))
        .group_by(StagingItem.chain_id, StagingItem.item_code)
        .subquery()
    )

    stmt = pg_insert(ProductVariant).from_select(
        ["chain_id", "item_code", "barcode", "item_type", "raw_name_he", "is_weighted"],
        select(
            latest.c.chain_id,
            latest.c.item_code,
            latest.c.barcode,
            latest.c.item_type,
            latest.c.raw_name_he,
            func.coalesce(latest.c.is_weighted, False),
        ),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["chain_id", "item_code"],
        set_={
            "barcode": stmt.excluded.barcode,
            "item_type": stmt.excluded.item_type,
            "raw_name_he": func.coalesce(stmt.excluded.raw_name_he, ProductVariant.raw_name_he),
            "is_weighted": stmt.excluded.is_weighted,
        },
    )
    # rowcount is -1 for an INSERT ... FROM SELECT that matched nothing.
    return max(session.execute(stmt).rowcount, 0)


def _upsert_canonicals(session: Session) -> tuple[int, int, int]:
    """Promote barcodes that clear the chain threshold into canonical products.

    The display name is the one the most chains use, which is a better label
    than any single chain's phrasing and avoids one chain's house style
    becoming the product's identity.
    """
    threshold = min_chain_count()

    spread = (
        select(
            ProductVariant.barcode.label("barcode"),
            func.count(func.distinct(ProductVariant.chain_id)).label("chain_count"),
        )
        .where(ProductVariant.barcode.isnot(None))
        .group_by(ProductVariant.barcode)
        .subquery()
    )
    seen = session.scalar(select(func.count()).select_from(spread)) or 0

    # The most common name per barcode, ties broken by the longer name, which
    # in practice is the more descriptive one.
    ranked = (
        select(
            ProductVariant.barcode.label("barcode"),
            ProductVariant.raw_name_he.label("name_he"),
            func.row_number()
            .over(
                partition_by=ProductVariant.barcode,
                order_by=(
                    func.count().desc(),
                    func.length(ProductVariant.raw_name_he).desc(),
                ),
            )
            .label("rank"),
        )
        .where(ProductVariant.barcode.isnot(None))
        .group_by(ProductVariant.barcode, ProductVariant.raw_name_he)
        .subquery()
    )

    eligible = (
        select(spread.c.barcode, ranked.c.name_he, spread.c.chain_count)
        .join(ranked, ranked.c.barcode == spread.c.barcode)
        .where(ranked.c.rank == 1, spread.c.chain_count >= threshold)
    )

    rows = session.execute(eligible).all()
    if not rows:
        return seen, 0, 0

    existing = {
        barcode
        for (barcode,) in session.execute(
            select(CanonicalProduct.barcode).where(
                CanonicalProduct.barcode.in_([row.barcode for row in rows])
            )
        )
    }

    payload = []
    for row in rows:
        pack = parse_pack(row.name_he)
        payload.append(
            {
                "barcode": row.barcode,
                "name_he": row.name_he,
                "pack_count": pack.pack_count,
                "unit_size": pack.unit_size,
                "unit_of_measure": pack.unit_of_measure,
                "chain_count": row.chain_count,
                "updated_at": func.now(),
            }
        )

    stmt = pg_insert(CanonicalProduct).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=["barcode"],
        set_={
            "name_he": stmt.excluded.name_he,
            "pack_count": stmt.excluded.pack_count,
            "unit_size": stmt.excluded.unit_size,
            "unit_of_measure": stmt.excluded.unit_of_measure,
            "chain_count": stmt.excluded.chain_count,
            "updated_at": func.now(),
        },
    )
    session.execute(stmt)

    created = sum(1 for row in rows if row.barcode not in existing)
    return seen, created, len(rows) - created


def _link_variants(session: Session) -> tuple[int, int]:
    """Attach variants to canonical products by exact barcode.

    Variants whose barcode never reached the threshold are explicitly unlinked
    rather than left stale, so a product dropping below the threshold stops
    being compared instead of quietly keeping an old link.
    """
    linked = session.execute(
        update(ProductVariant)
        .where(
            ProductVariant.barcode.isnot(None),
            ProductVariant.barcode == CanonicalProduct.barcode,
        )
        .values(
            canonical_id=CanonicalProduct.id,
            match_method=MATCH_BARCODE_EXACT,
            match_confidence=BARCODE_CONFIDENCE,
        )
    ).rowcount or 0

    unlinked = session.execute(
        update(ProductVariant)
        .where(
            ProductVariant.canonical_id.isnot(None),
            ~select(CanonicalProduct.id)
            .where(CanonicalProduct.id == ProductVariant.canonical_id)
            .exists(),
        )
        .values(canonical_id=None, match_method=None, match_confidence=None)
    ).rowcount or 0

    return linked, unlinked


def _refresh_chain_counts(session: Session) -> None:
    """Keep chain_count honest after linking, since it drives inclusion."""
    session.execute(
        text(
            """
            UPDATE canonical_products cp
               SET chain_count = sub.chain_count
              FROM (
                    SELECT canonical_id, COUNT(DISTINCT chain_id) AS chain_count
                      FROM product_variants
                     WHERE canonical_id IS NOT NULL
                     GROUP BY canonical_id
                   ) sub
             WHERE cp.id = sub.canonical_id
               AND cp.chain_count IS DISTINCT FROM sub.chain_count
            """
        )
    )


def purge_staging(session: Session, keep_run_id: int | None = None) -> int:
    """Staging is scratch space; keeping it past a cycle only costs disk."""
    stmt = delete(StagingItem)
    if keep_run_id is not None:
        stmt = stmt.where(StagingItem.run_id != keep_run_id)
    # rowcount is -1 for an INSERT ... FROM SELECT that matched nothing.
    return max(session.execute(stmt).rowcount, 0)
