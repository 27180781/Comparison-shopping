"""Fetching published files, one chain at a time.

A thin wrapper over il-supermarket-scraper. Deliberately thin: thirteen
scrapers across five portal families is months of maintenance nobody should
pay twice, and the library carries daily tests that catch a chain changing its
interface (ADR-001).

Two things the library gets wrong that are handled here:

  * `ScarpingTask.start()` spawns a daemon thread and returns immediately.
    Without `join()` the process exits and the download dies mid-flight, with
    no error and no files -- indistinguishable from geo-blocking (F-12).
"""

from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ingestion.config import settings

log = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    scraper_name: str
    files: list[Path] = field(default_factory=list)
    bytes_downloaded: int = 0
    error: str | None = None
    skipped_unstable: bool = False

    @property
    def status(self) -> str:
        if self.error:
            return "failed"
        if self.skipped_unstable:
            return "skipped_unstable"
        return "ok" if self.files else "no_files"


def is_disabled_upstream(scraper_name: str) -> bool:
    """True when the library itself has switched this scraper off.

    It does that for chains it knows to be broken, and the result is a run that
    looks successful with zero files. Detecting it here is what lets
    ingestion_runs record `skipped_unstable` instead of a false success (F-5).
    """
    try:
        from il_supermarket_scarper.scrappers_factory import ScraperFactory

        return ScraperFactory.get(scraper_name) is None
    except (ImportError, ValueError):
        return False


def download_chain(
    scraper_name: str,
    file_types: list[str],
    limit: int | None = None,
    output_root: Path | None = None,
) -> DownloadResult:
    """Fetch one chain's files. Never raises -- ingestion is best-effort."""
    from il_supermarket_scarper import ScarpingTask

    root = output_root or settings.storage_path
    root.mkdir(parents=True, exist_ok=True)

    if is_disabled_upstream(scraper_name):
        log.info("%s is disabled upstream; skipping", scraper_name)
        return DownloadResult(scraper_name=scraper_name, skipped_unstable=True)

    before = _snapshot(root)
    try:
        task = ScarpingTask(
            enabled_scrapers=[scraper_name],
            files_types=file_types,
            multiprocessing=settings.number_of_processes,
            timeout_in_seconds=settings.scraper_timeout_seconds,
            output_configuration={"output_mode": "disk", "base_storage_path": str(root)},
            status_configuration={
                "database_type": "json",
                "base_path": str(root / "status"),
            },
        )
        task.start(limit=limit) if limit else task.start()
        task.join()  # without this the daemon thread is killed mid-download
    except Exception as exc:  # noqa: BLE001 - one chain must not stop the rest
        log.exception("scraping %s failed", scraper_name)
        return DownloadResult(scraper_name=scraper_name, error=str(exc))

    new_files = sorted(_snapshot(root) - before)
    return DownloadResult(
        scraper_name=scraper_name,
        files=new_files,
        bytes_downloaded=sum(p.stat().st_size for p in new_files if p.exists()),
    )


def _snapshot(root: Path) -> set[Path]:
    """Files present under root, ignoring the library's own status directory."""
    if not root.is_dir():
        return set()
    return {
        path
        for path in root.rglob("*")
        if path.is_file() and "status" not in path.relative_to(root).parts
    }


def discard(paths: list[Path]) -> None:
    """Delete local copies once archived and normalised.

    Left alone this fills the disk: a full run is 5-15GB a day.
    """
    if settings.keep_local_files:
        return
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("could not remove %s: %s", path, exc)


def clear_chain_folder(root: Path, chain_folder: str) -> None:
    target = root / chain_folder
    if target.is_dir() and not settings.keep_local_files:
        shutil.rmtree(target, ignore_errors=True)
