"""Runtime configuration, read from the environment.

Every tunable lives in .env (see .env.example). Nothing that a human might
want to change on a running system is written into the code -- especially
match thresholds, search radii and travel penalties, per CLAUDE.md.

Source URLs and scraper names are deliberately absent here: they belong in the
`chains` table so switching a portal is an UPDATE rather than a deploy. Kitty
already moved once and the library warns two more are likely (ADR-006).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(REPO_ROOT / ".env")


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


def _list(name: str, default: str = "") -> list[str]:
    return [part.strip() for part in _str(name, default).split(",") if part.strip()]


@dataclass(frozen=True)
class R2Settings:
    """Cloudflare R2, reached over the S3 API."""

    account_id: str = field(default_factory=lambda: _str("R2_ACCOUNT_ID"))
    access_key_id: str = field(default_factory=lambda: _str("R2_ACCESS_KEY_ID"))
    secret_access_key: str = field(default_factory=lambda: _str("R2_SECRET_ACCESS_KEY"))
    bucket: str = field(default_factory=lambda: _str("R2_BUCKET", "price-raw"))
    endpoint: str = field(default_factory=lambda: _str("R2_ENDPOINT"))

    @property
    def configured(self) -> bool:
        return bool(self.access_key_id and self.secret_access_key and self.endpoint)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _str("DATABASE_URL"))
    redis_url: str = field(default_factory=lambda: _str("REDIS_URL"))

    enabled_scrapers: list[str] = field(default_factory=lambda: _list("ENABLED_SCRAPERS"))
    enabled_file_types: list[str] = field(
        default_factory=lambda: _list("ENABLED_FILE_TYPES", "STORE_FILE,PRICE_FULL_FILE")
    )
    number_of_processes: int = field(default_factory=lambda: _int("NUMBER_OF_PROCESSES", 5))
    storage_path: Path = field(default_factory=lambda: Path(_str("STORAGE_PATH", "./dumps")))
    scraper_timeout_seconds: int = field(
        default_factory=lambda: _int("SCRAPER_TIMEOUT_SECONDS", 30 * 60)
    )
    files_per_chain_limit: int = field(default_factory=lambda: _int("FILES_PER_CHAIN_LIMIT", 0))

    # A chain that returns far fewer files than usual has usually changed its
    # portal rather than its assortment. Phase 1 acceptance criterion.
    volume_drop_alert_pct: int = field(default_factory=lambda: _int("VOLUME_DROP_ALERT_PCT", 50))
    volume_baseline_runs: int = field(default_factory=lambda: _int("VOLUME_BASELINE_RUNS", 7))

    keep_local_files: bool = field(
        default_factory=lambda: _str("KEEP_LOCAL_FILES", "false").lower() == "true"
    )

    r2: R2Settings = field(default_factory=R2Settings)

    def require_database(self) -> str:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env.")
        return self.database_url


settings = Settings()
