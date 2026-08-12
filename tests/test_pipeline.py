"""Pipeline behaviour that does not need the network.

The download split matters more than it looks: a chain whose store file loses
the file budget stages its prices and then drops every one of them, while
reporting a clean success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ingestion import pipeline
from ingestion.download import DownloadResult
from tests.conftest import requires_db


@dataclass
class FakeChain:
    scraper_name: str = "SHUFERSAL"
    name_he: str = "שופרסל"
    id: int = 1


@dataclass
class Recorder:
    calls: list[tuple[tuple[str, ...], int | None]] = field(default_factory=list)

    def __call__(self, scraper_name, file_types, limit=None, output_root=None):
        self.calls.append((tuple(file_types), limit))
        return DownloadResult(
            scraper_name=scraper_name,
            files=[Path(f"/tmp/{kind}.xml") for kind in file_types],
            bytes_downloaded=len(file_types),
        )


def test_store_files_are_fetched_separately_and_unlimited(monkeypatch):
    """Otherwise the store file competes with hundreds of price files and loses.

    A chain with no stores stages prices that can never be attached.
    """
    recorder = Recorder()
    monkeypatch.setattr(pipeline.download, "download_chain", recorder)

    pipeline._download(FakeChain(), ["STORE_FILE", "PRICE_FULL_FILE", "PROMO_FULL_FILE"], 12)

    assert recorder.calls == [
        (("STORE_FILE",), None),
        (("PRICE_FULL_FILE", "PROMO_FULL_FILE"), 12),
    ]


def test_a_request_without_store_files_stays_a_single_download(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(pipeline.download, "download_chain", recorder)

    pipeline._download(FakeChain(), ["PRICE_FILE", "PROMO_FILE"], 3)

    assert recorder.calls == [(("PRICE_FILE", "PROMO_FILE"), 3)]


def test_both_halves_are_reported_as_one_result(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(pipeline.download, "download_chain", recorder)

    result = pipeline._download(FakeChain(), ["STORE_FILE", "PRICE_FULL_FILE"], 5)

    assert len(result.files) == 2
    assert result.bytes_downloaded == 2
    assert result.status == "ok"


def test_a_failed_store_fetch_fails_the_chain(monkeypatch):
    """Prices without stores go nowhere, so this is the half that matters."""

    def failing(scraper_name, file_types, limit=None, output_root=None):
        if file_types == ["STORE_FILE"]:
            return DownloadResult(scraper_name=scraper_name, error="portal timed out")
        return DownloadResult(scraper_name=scraper_name, files=[Path("/tmp/p.xml")])

    monkeypatch.setattr(pipeline.download, "download_chain", failing)
    result = pipeline._download(FakeChain(), ["STORE_FILE", "PRICE_FULL_FILE"], 5)

    assert result.status == "failed"
    assert "portal timed out" in result.error


# ─── incremental ingestion ─────────────────────────────────────────────────


@requires_db
def test_a_file_already_ingested_is_not_parsed_again(session, engine, monkeypatch, tmp_path):
    """The chains republish the same snapshot all day. Parsing it twice is waste.

    Not a micro-optimisation: a full Shufersal snapshot is hundreds of files
    and gigabytes, and an hourly cycle that re-reads all of it to rediscover
    prices it already holds cannot run hourly.
    """
    import ingestion.db as db_module
    from sqlalchemy.orm import sessionmaker

    from ingestion.models import Chain, IngestedFile

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(db_module, "session_factory", lambda: factory)

    chain = Chain(name_he="שופרסל", scraper_name="SHUFERSAL", portal_type="shufersal")
    session.add(chain)
    session.commit()

    published = tmp_path / "PriceFull7290027600007-001-202608120800.xml"
    published.write_text("<root/>", encoding="utf-8")

    parsed: list[str] = []

    def fake_download(_chain, _types, _limit):
        return DownloadResult(scraper_name="SHUFERSAL", files=[published], bytes_downloaded=7)

    def fake_load_items(_session, _chain, _run_id, path, _when, _key):
        parsed.append(path.name)
        return 3

    monkeypatch.setattr(pipeline, "_download", fake_download)
    monkeypatch.setattr(pipeline, "_load_items", fake_load_items)
    monkeypatch.setattr(pipeline.download, "discard", lambda paths: None)

    first = pipeline._ingest_chain(chain, ["PRICE_FULL_FILE"], None, _NoArchive())
    second = pipeline._ingest_chain(chain, ["PRICE_FULL_FILE"], None, _NoArchive())

    assert parsed == [published.name], "the second run re-parsed a file it already held"
    assert first.rows == 3 and first.skipped_files == 0
    assert second.rows == 0 and second.skipped_files == 1
    # A run with nothing new is healthy, not silent -- alerting on it would
    # page someone every hour of a working system.
    assert second.status == "unchanged"
    assert session.query(IngestedFile).count() == 1


@requires_db
def test_a_republished_file_of_a_different_size_is_read_again(session, engine, monkeypatch, tmp_path):
    """Same name, new bytes: the chain corrected the file and we want the fix."""
    import ingestion.db as db_module
    from sqlalchemy.orm import sessionmaker

    from ingestion.models import Chain

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(db_module, "session_factory", lambda: factory)

    chain = Chain(name_he="שופרסל", scraper_name="SHUFERSAL", portal_type="shufersal")
    session.add(chain)
    session.commit()

    published = tmp_path / "PriceFull7290027600007-001-202608120800.xml"
    published.write_text("<root/>", encoding="utf-8")

    parsed: list[int] = []
    monkeypatch.setattr(
        pipeline, "_download",
        lambda *_: DownloadResult(scraper_name="SHUFERSAL", files=[published]),
    )
    monkeypatch.setattr(
        pipeline, "_load_items",
        lambda _s, _c, _r, path, _w, _k: (parsed.append(path.stat().st_size), 1)[1],
    )
    monkeypatch.setattr(pipeline.download, "discard", lambda paths: None)

    pipeline._ingest_chain(chain, ["PRICE_FULL_FILE"], None, _NoArchive())
    published.write_text("<root><item/></root>", encoding="utf-8")
    outcome = pipeline._ingest_chain(chain, ["PRICE_FULL_FILE"], None, _NoArchive())

    assert len(parsed) == 2
    assert outcome.skipped_files == 0


class _NoArchive:
    """R2 is optional; ingestion must work without it."""

    def put(self, *_args, **_kwargs):
        from ingestion.storage import UploadResult

        return UploadResult(key=None, uploaded=False)
