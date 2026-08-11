"""Command line entry point for the ingestion worker.

    python -m ingestion cycle            # download, normalise, catalog, prices
    python -m ingestion download         # fetch and stage only
    python -m ingestion catalog          # rebuild the catalog from staging
    python -m ingestion prices           # fold staging into price history
    python -m ingestion status           # what happened on the last runs

`cycle` is what cron calls. The steps are separable so a failure can be
resumed from where it stopped rather than re-downloading gigabytes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from catalog import build as catalog_build
from catalog import prices as catalog_prices
from ingestion import pipeline
from ingestion.config import _str, settings
from ingestion.db import session_scope
from ingestion.models import Chain, IngestionRun

log = logging.getLogger("ingestion")

FULL_TYPES = ["STORE_FILE", "PRICE_FULL_FILE", "PROMO_FULL_FILE"]
DELTA_TYPES = ["PRICE_FILE", "PROMO_FILE"]


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else _str("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        stream=sys.stdout,
    )


def cmd_download(args) -> int:
    types = DELTA_TYPES if args.deltas else FULL_TYPES
    outcomes = pipeline.run_cycle(file_types=types, only=args.chains, limit=args.limit)
    _report(outcomes)
    # Exit non-zero only when nothing at all worked: one chain failing is
    # expected and must not fail the cron job.
    return 0 if any(o.status in {"ok", "partial"} for o in outcomes) else 1


def cmd_catalog(args) -> int:
    with session_scope() as session:
        report = catalog_build.build(session)
    print(json.dumps(report.as_dict(), indent=2))
    return 0


def cmd_prices(args) -> int:
    with session_scope() as session:
        report = catalog_prices.rebuild(session)
    print(json.dumps(report.as_dict(), indent=2))
    return 0


def cmd_cycle(args) -> int:
    """One full pass. What the daily cron runs."""
    types = DELTA_TYPES if args.deltas else FULL_TYPES
    outcomes = pipeline.run_cycle(file_types=types, only=args.chains, limit=args.limit)
    _report(outcomes)

    if not any(o.status in {"ok", "partial"} for o in outcomes):
        log.error("no chain produced data; leaving the catalog untouched")
        return 1

    with session_scope() as session:
        build_report = catalog_build.build(session)
    log.info("catalog: %s", build_report.as_dict())

    with session_scope() as session:
        price_report = catalog_prices.rebuild(session)
    log.info("prices: %s", price_report.as_dict())

    # Staging has served its purpose once history is written; keeping it only
    # costs disk.
    with session_scope() as session:
        purged = catalog_build.purge_staging(session)
    log.info("purged %s staging rows", purged)
    return 0


def cmd_status(args) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    with session_scope() as session:
        rows = session.execute(
            select(
                Chain.name_he,
                IngestionRun.scraper_name,
                IngestionRun.status,
                IngestionRun.file_count,
                IngestionRun.row_count,
                IngestionRun.started_at,
                IngestionRun.error,
            )
            .join(Chain, Chain.id == IngestionRun.chain_id, isouter=True)
            .where(IngestionRun.started_at >= since)
            .order_by(IngestionRun.started_at.desc())
        ).all()

    if not rows:
        print(f"no ingestion runs in the last {args.hours}h")
        return 1

    print(f"{'CHAIN':<28} {'STATUS':<18} {'FILES':>7} {'ROWS':>10}  STARTED")
    print("-" * 88)
    for name, scraper, status, files, rows_count, started, error in rows:
        label = name or scraper or "?"
        print(f"{label:<28} {status:<18} {files:>7} {rows_count:>10}  {started:%Y-%m-%d %H:%M}")
        if error:
            print(f"{'':<28} └─ {error.strip().splitlines()[0][:70]}")
    return 0


def _report(outcomes) -> None:
    print(f"\n{'CHAIN':<28} {'STATUS':<18} {'FILES':>7} {'ROWS':>10}")
    print("-" * 66)
    for outcome in outcomes:
        print(
            f"{outcome.chain:<28} {outcome.status:<18} {outcome.files:>7} {outcome.rows:>10}"
        )
        if outcome.volume_warning:
            print(f"{'':<28} ⚠ {outcome.volume_warning}")
        if outcome.error:
            print(f"{'':<28} ✖ {outcome.error.strip().splitlines()[0][:60]}")

    ok = sum(1 for o in outcomes if o.status in {"ok", "partial"})
    print(f"\n{ok}/{len(outcomes)} chains produced data")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ingestion", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler, needs_download in (
        ("cycle", cmd_cycle, True),
        ("download", cmd_download, True),
        ("catalog", cmd_catalog, False),
        ("prices", cmd_prices, False),
    ):
        cmd = sub.add_parser(name, help=handler.__doc__)
        cmd.set_defaults(handler=handler)
        if needs_download:
            cmd.add_argument("--chains", nargs="*", help="scraper names; default all active")
            cmd.add_argument(
                "--deltas",
                action="store_true",
                help="hourly deltas instead of the daily full snapshots",
            )
            cmd.add_argument("--limit", type=int, default=None, help="max files per chain")

    status = sub.add_parser("status", help=cmd_status.__doc__)
    status.set_defaults(handler=cmd_status)
    status.add_argument("--hours", type=int, default=24)

    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    if not settings.database_url:
        print("DATABASE_URL is not set. Copy .env.example to .env.", file=sys.stderr)
        return 2

    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
