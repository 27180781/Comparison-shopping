"""FastAPI application.

Serves the Hebrew/RTL frontend built in Lovable, so CORS is open by
configuration rather than hardcoded.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api import deps
from api.routers import basket, catalog, coverage, search
from api.schemas import HealthResponse
from catalog.models import CanonicalProduct
from ingestion.config import _list, _str
from ingestion.models import HEALTHY_STATUSES, Chain, IngestionRun

logging.basicConfig(
    level=_str("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)

app = FastAPI(
    title="השוואת מחירי מזון",
    description=(
        "השוואת מחירים לצרכן, מבוססת נתוני שקיפות המחירים שרשתות המזון "
        "מחויבות לפרסם לפי חוק. המחיר בקופה גובר."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_list("CORS_ORIGINS", "*") or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(search.router)
app.include_router(catalog.router)
app.include_router(basket.router)
app.include_router(coverage.router)

# The Hebrew/RTL interface, served by the same process as the API it calls.
# No build step and no separate origin: a static bundle next to the API is one
# fewer thing to deploy and removes CORS from the picture entirely. The docs
# name Lovable for production; this is what makes the data visible today.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="web")

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse("/app/")

# A chain that has not been ingested for longer than this has a broken portal,
# not a quiet week.
STALE_AFTER_HOURS = 36


@app.get("/health", response_model=HealthResponse)
def health(session: Session = Depends(deps.get_session)) -> HealthResponse:
    """Liveness plus the two facts that matter operationally.

    Whether the data is fresh, and which chains have gone quiet. A chain
    failing silently looks exactly like a chain with nothing to report, which
    is why staleness is surfaced rather than inferred.
    """
    try:
        active = session.scalar(
            select(func.count()).select_from(Chain).where(Chain.is_active.is_(True))
        )
        products = session.scalar(select(func.count()).select_from(CanonicalProduct))
        last_run = session.scalar(select(func.max(IngestionRun.finished_at)))

        cutoff = datetime.now(timezone.utc) - timedelta(hours=STALE_AFTER_HOURS)
        stale = list(
            session.scalars(
                select(Chain.name_he)
                .where(Chain.is_active.is_(True))
                .where(
                    ~select(IngestionRun.id)
                    .where(
                        IngestionRun.chain_id == Chain.id,
                        IngestionRun.finished_at >= cutoff,
                        IngestionRun.status.in_(HEALTHY_STATUSES),
                    )
                    .exists()
                )
            )
        )
        return HealthResponse(
            status="ok",
            database=True,
            chains_active=active,
            products=products,
            last_ingestion=last_run,
            stale_chains=stale,
        )
    except SQLAlchemyError:
        logging.exception("health check could not reach the database")
        return HealthResponse(status="degraded", database=False)
