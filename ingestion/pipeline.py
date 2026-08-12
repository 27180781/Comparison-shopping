"""Run one ingestion cycle: fetch, archive, normalise, record.

Best-effort by construction. Every chain is independent, every outcome is
written to `ingestion_runs`, and a chain that explodes is logged and stepped
over rather than raised -- one portal going down at 3am must not cost the
other eleven their daily prices.

The health check is the part that is easy to skip and expensive to skip: a
chain that quietly returns a fraction of its usual files has almost always
changed its portal, and without an alert that looks like a successful run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from catalog.barcode import normalize_barcode
from ingestion import download, normalize
from ingestion.config import settings
from ingestion.db import session_scope
from ingestion.models import (
    HEALTHY_STATUSES,
    Chain,
    IngestedFile,
    IngestionRun,
    PriceGroup,
    StagingItem,
    Store,
)
from ingestion.storage import RawArchive

log = logging.getLogger(__name__)

STAGING_BATCH = 5_000


@dataclass
class ChainOutcome:
    chain: str
    scraper: str
    status: str
    files: int
    rows: int
    error: str | None = None
    volume_warning: str | None = None
    # Files that arrived but were already in the database from an earlier run.
    skipped_files: int = 0


def active_chains(session: Session, only: list[str] | None = None) -> list[Chain]:
    query = select(Chain).where(Chain.is_active.is_(True)).order_by(Chain.id)
    chains = list(session.scalars(query))
    if only:
        wanted = {name.strip().upper() for name in only}
        chains = [chain for chain in chains if chain.scraper_name.upper() in wanted]
    return chains


def run_cycle(
    file_types: list[str] | None = None,
    only: list[str] | None = None,
    limit: int | None = None,
) -> list[ChainOutcome]:
    """Ingest every active chain once."""
    types = file_types or settings.enabled_file_types
    limit = limit if limit is not None else (settings.files_per_chain_limit or None)
    archive = RawArchive()
    outcomes: list[ChainOutcome] = []

    with session_scope() as session:
        chains = active_chains(session, only)

    for chain in chains:
        try:
            outcomes.append(_ingest_chain(chain, types, limit, archive))
        except Exception as exc:  # noqa: BLE001 - isolation is the whole point
            log.exception("chain %s failed outside the recorded path", chain.scraper_name)
            outcomes.append(
                ChainOutcome(
                    chain=chain.name_he,
                    scraper=chain.scraper_name,
                    status="failed",
                    files=0,
                    rows=0,
                    error=str(exc),
                )
            )
    return outcomes


def _ingest_chain(
    chain: Chain, file_types: list[str], limit: int | None, archive: RawArchive
) -> ChainOutcome:
    started = datetime.now(timezone.utc)
    log.info("ingesting %s (%s)", chain.name_he, chain.scraper_name)

    result = _download(chain, file_types, limit)

    rows = 0
    stores_seen = 0
    skipped = 0
    with session_scope() as session:
        run = IngestionRun(
            chain_id=chain.id,
            scraper_name=chain.scraper_name,
            file_types=",".join(file_types),
            started_at=started,
            status=result.status,
            error=result.error,
            file_count=len(result.files),
            bytes_downloaded=result.bytes_downloaded,
        )
        session.add(run)
        session.flush()
        run_id = run.id

        known = _already_ingested(session, chain)

        for path in result.files:
            size = path.stat().st_size if path.exists() else 0
            # The whole point of the registry: a chain republishing the same
            # snapshot every hour costs nothing after the first time.
            if known.get(path.name) == size:
                skipped += 1
                continue

            kind = normalize.classify(path.name)
            when = normalize.file_date(path) or started.date()
            upload = archive.put(chain.scraper_name, kind, path, when)

            try:
                file_rows = 0
                if kind == "stores":
                    stores_seen += _load_stores(session, chain, path)
                elif kind in {"price_full", "price_delta"}:
                    file_rows = _load_items(session, chain, run_id, path, when, upload.key)
                    rows += file_rows
                _remember_file(session, chain, path, kind, when, size, file_rows, upload.key)
            except Exception as exc:  # noqa: BLE001 - a bad file is not a bad chain
                log.warning("could not parse %s: %s", path.name, exc)
                run.status = "partial"
                run.error = (run.error or "") + f"\n{path.name}: {exc}"

        if skipped:
            log.info(
                "%s: %s of %s files were already ingested and were not re-parsed",
                chain.name_he,
                skipped,
                len(result.files),
            )

        run.row_count = rows
        run.finished_at = datetime.now(timezone.utc)
        if run.status == "ok" and not rows and not stores_seen:
            # A run that found only files it already holds is a healthy run
            # with nothing to do, which is not the same as a portal that
            # served nothing -- and only one of the two is worth an alert.
            run.status = "unchanged" if skipped else "no_files"

        # Prices for a chain with no stores cannot be attached to anything, so
        # they are staged and then dropped on the floor. Without this the run
        # reports 164,176 rows and a clean "ok" while contributing nothing.
        if rows and not session.scalar(
            select(func.count()).select_from(Store).where(Store.chain_id == chain.id)
        ):
            run.status = "partial"
            run.error = (
                (run.error or "")
                + "\nstaged prices but this chain has no stores; the rows cannot be "
                "attached and will be discarded. Fetch STORE_FILE for this chain."
            ).strip()
            log.warning(
                "%s staged %s price rows but has no stores on record; they will be discarded",
                chain.name_he,
                rows,
            )

        status = run.status

        warning = _volume_warning(session, chain, len(result.files))

    download.discard(result.files)

    if warning:
        log.warning("%s: %s", chain.name_he, warning)

    return ChainOutcome(
        chain=chain.name_he,
        scraper=chain.scraper_name,
        status=status,
        files=len(result.files),
        rows=rows,
        error=result.error,
        volume_warning=warning,
        skipped_files=skipped,
    )


def _already_ingested(session: Session, chain: Chain) -> dict[str, int]:
    """File name to size, for everything this chain has already contributed."""
    return {
        name: size
        for name, size in session.execute(
            select(IngestedFile.file_name, IngestedFile.size_bytes).where(
                IngestedFile.chain_id == chain.id
            )
        ).all()
    }


def _remember_file(
    session: Session,
    chain: Chain,
    path: Path,
    kind: str,
    when,
    size: int,
    rows: int,
    source_key: str | None,
) -> None:
    """Record a file as ingested, so the next run steps over it.

    Written only after the file parsed, so a file that blew up is retried on
    the next cycle rather than being remembered as done.
    """
    session.execute(
        pg_insert(IngestedFile)
        .values(
            chain_id=chain.id,
            file_name=path.name,
            kind=kind,
            file_date=when,
            size_bytes=size,
            row_count=rows,
            source_key=source_key,
        )
        .on_conflict_do_update(
            index_elements=["chain_id", "file_name"],
            set_={
                "size_bytes": size,
                "row_count": rows,
                "file_date": when,
                "source_key": source_key,
                "ingested_at": datetime.now(timezone.utc),
            },
        )
    )


STORE_FILE = "STORE_FILE"


def _download(chain: Chain, file_types: list[str], limit: int | None) -> download.DownloadResult:
    """Fetch a chain, never letting the store file compete for the file budget.

    `limit` caps files per chain across all requested types, so on a chain that
    publishes hundreds of price files the store file simply never gets picked --
    and prices for a chain with no stores are staged and then silently dropped,
    because there is nothing to attach them to. Store files are one per chain
    and a few KB, so they are fetched separately and unlimited.
    """
    wanted = [kind for kind in file_types if kind != STORE_FILE]
    if STORE_FILE not in file_types:
        return download.download_chain(chain.scraper_name, wanted, limit)

    stores = download.download_chain(chain.scraper_name, [STORE_FILE], None)
    if not wanted:
        return stores

    rest = download.download_chain(chain.scraper_name, wanted, limit)
    return download.DownloadResult(
        scraper_name=chain.scraper_name,
        files=stores.files + rest.files,
        bytes_downloaded=stores.bytes_downloaded + rest.bytes_downloaded,
        # A failure in either half is the chain's failure; the store fetch
        # failing matters most, since prices without stores go nowhere.
        error=stores.error or rest.error,
        skipped_unstable=stores.skipped_unstable and rest.skipped_unstable,
    )


def _price_group_id(session: Session, chain: Chain, code: str | None, label: str | None) -> int | None:
    """Resolve, creating on first sight. The published SubChain is the price group."""
    if not code:
        return None
    stmt = (
        pg_insert(PriceGroup)
        .values(chain_id=chain.id, sub_chain_code=code, label=label)
        .on_conflict_do_nothing(index_elements=["chain_id", "sub_chain_code"])
    )
    session.execute(stmt)
    return session.scalar(
        select(PriceGroup.id).where(
            PriceGroup.chain_id == chain.id, PriceGroup.sub_chain_code == code
        )
    )


def _load_stores(session: Session, chain: Chain, path: Path) -> int:
    """Upsert stores. Chains republish the same file daily, so this is idempotent."""
    seen = 0
    now = datetime.now(timezone.utc)
    for _header, row in normalize.iter_stores(path):
        # Maayan2000 publishes SubChainName as "1", so a numeric label is
        # noise rather than a price-group name.
        label = row.sub_chain_name if (row.sub_chain_name or "").strip().isdigit() is False else None
        group_id = _price_group_id(session, chain, row.sub_chain_code, label)

        stmt = (
            pg_insert(Store)
            .values(
                chain_id=chain.id,
                price_group_id=group_id,
                store_code=row.store_code,
                name_he=row.name_he,
                address=row.address,
                city=row.city,
                zip_code=row.zip_code,
                store_type=row.store_type,
                last_seen_at=now,
            )
            .on_conflict_do_update(
                index_elements=["chain_id", "store_code"],
                set_={
                    "price_group_id": group_id,
                    "name_he": row.name_he,
                    "address": row.address,
                    "city": row.city,
                    "zip_code": row.zip_code,
                    "store_type": row.store_type,
                    "last_seen_at": now,
                },
            )
        )
        session.execute(stmt)
        seen += 1
    return seen


def _load_items(
    session: Session,
    chain: Chain,
    run_id: int,
    path: Path,
    when,
    source_key: str | None,
) -> int:
    """Stream a price file into staging.

    Barcode normalisation happens here so the catalog stage can filter on an
    indexed column. ItemType is recorded but never trusted as a filter -- Phase
    0 found it set to 1 on 99.99% of items including single-digit internal codes.
    """
    batch: list[dict] = []
    count = 0

    for _header, item in normalize.iter_items(path):
        batch.append(
            {
                "run_id": run_id,
                "chain_id": chain.id,
                "sub_chain_code": item.sub_chain_code,
                "store_code": item.store_code,
                "item_code": item.item_code,
                "barcode": normalize_barcode(item.item_code),
                "item_type": item.item_type,
                "raw_name_he": item.raw_name_he,
                "manufacturer": item.manufacturer,
                "unit_qty": item.unit_qty,
                "quantity": item.quantity,
                "unit_of_measure": item.unit_of_measure,
                "is_weighted": item.is_weighted,
                "price": item.price,
                "unit_price": item.unit_price,
                "price_updated_at": item.price_updated_at,
                "file_date": when,
                "source_key": source_key,
            }
        )
        if len(batch) >= STAGING_BATCH:
            session.execute(StagingItem.__table__.insert(), batch)
            count += len(batch)
            batch.clear()

    if batch:
        session.execute(StagingItem.__table__.insert(), batch)
        count += len(batch)
    return count


def _volume_warning(session: Session, chain: Chain, files: int) -> str | None:
    """Alert when a chain's file volume collapses against its own recent average.

    Phase 1 acceptance criterion. The comparison is per chain because a healthy
    volume for Maayan2000 and for Shufersal differ by two orders of magnitude.
    """
    baseline = session.scalar(
        select(func.avg(IngestionRun.file_count)).where(
            IngestionRun.chain_id == chain.id,
            IngestionRun.status.in_(HEALTHY_STATUSES),
            IngestionRun.id.in_(
                select(IngestionRun.id)
                .where(IngestionRun.chain_id == chain.id)
                .order_by(IngestionRun.started_at.desc())
                .limit(settings.volume_baseline_runs)
            ),
        )
    )
    if not baseline or baseline <= 0:
        return None

    threshold = float(baseline) * (1 - settings.volume_drop_alert_pct / 100)
    if files < threshold:
        return (
            f"file volume dropped to {files} against a {float(baseline):.0f} "
            f"average over the last {settings.volume_baseline_runs} runs -- "
            "the portal has probably changed"
        )
    return None
