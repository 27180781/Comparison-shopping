"""What the catalogue covers.

Not an operator endpoint. A comparison that quietly spans two chains out of
twelve is a different claim from one that spans all of them, and the interface
is expected to say which (docs/01-SPEC.md §6, ADR-010). It doubles as the
fastest way to see whether a chain ingested prices but has no stores to attach
them to.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api import deps
from api.schemas import ChainCoverage, CoverageResponse
from catalog.models import CanonicalProduct, PriceCurrent, ProductVariant
from ingestion.models import HEALTHY_STATUSES, Chain, IngestionRun, Store

router = APIRouter(tags=["coverage"])


@router.get("/coverage", response_model=CoverageResponse)
def coverage(session: Session = Depends(deps.get_session)) -> CoverageResponse:
    stores = (
        select(
            Store.chain_id.label("chain_id"),
            func.count().label("stores"),
            func.count(Store.lat).label("geocoded"),
        )
        .group_by(Store.chain_id)
        .subquery()
    )
    variants = (
        select(ProductVariant.chain_id.label("chain_id"), func.count().label("variants"))
        .group_by(ProductVariant.chain_id)
        .subquery()
    )
    priced = (
        select(ProductVariant.chain_id.label("chain_id"), func.count().label("priced"))
        .join(PriceCurrent, PriceCurrent.variant_id == ProductVariant.id)
        .group_by(ProductVariant.chain_id)
        .subquery()
    )
    last_run = (
        select(
            IngestionRun.chain_id.label("chain_id"),
            func.max(IngestionRun.finished_at).label("finished_at"),
        )
        .where(IngestionRun.status.in_(HEALTHY_STATUSES))
        .group_by(IngestionRun.chain_id)
        .subquery()
    )

    rows = session.execute(
        select(
            Chain.id,
            Chain.name_he,
            func.coalesce(stores.c.stores, 0),
            func.coalesce(stores.c.geocoded, 0),
            func.coalesce(variants.c.variants, 0),
            func.coalesce(priced.c.priced, 0),
            last_run.c.finished_at,
        )
        .outerjoin(stores, stores.c.chain_id == Chain.id)
        .outerjoin(variants, variants.c.chain_id == Chain.id)
        .outerjoin(priced, priced.c.chain_id == Chain.id)
        .outerjoin(last_run, last_run.c.chain_id == Chain.id)
        .where(Chain.is_active.is_(True))
        .order_by(func.coalesce(priced.c.priced, 0).desc(), Chain.name_he)
    ).all()

    return CoverageResponse(
        chains=[
            ChainCoverage(
                chain_id=row[0],
                name_he=row[1],
                stores=row[2],
                stores_geocoded=row[3],
                variants=row[4],
                priced_rows=row[5],
                last_ingested=row[6],
            )
            for row in rows
        ],
        products=session.scalar(select(func.count()).select_from(CanonicalProduct)) or 0,
        # The number that matters for honest comparison: a product carried by
        # one chain has nothing to be compared against.
        products_multi_chain=session.scalar(
            select(func.count()).select_from(CanonicalProduct).where(
                CanonicalProduct.chain_count > 1
            )
        )
        or 0,
        stores_total=session.scalar(select(func.count()).select_from(Store)) or 0,
        stores_geocoded=session.scalar(select(func.count(Store.lat))) or 0,
        prices_total=session.scalar(select(func.count()).select_from(PriceCurrent)) or 0,
        last_updated=session.scalar(select(func.max(PriceCurrent.updated_at))),
    )
