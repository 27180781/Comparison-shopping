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
