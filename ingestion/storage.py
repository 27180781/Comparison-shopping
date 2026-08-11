"""Raw file archive on Cloudflare R2.

Every downloaded file is kept exactly as published, at
`raw/{chain}/{date}/{type}/{store}.xml.gz`. This is not a cache -- it is the
audit trail. When a parser turns out to be wrong six months from now, the only
way to fix history is to re-parse the original bytes, and the retailers do not
serve yesterday's files.

R2 is optional: without credentials the pipeline still runs and normalises,
it just does not archive. That keeps local development from needing a bucket.
"""

from __future__ import annotations

import gzip
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ingestion.config import settings

log = logging.getLogger(__name__)


@dataclass
class UploadResult:
    key: str | None
    uploaded: bool
    error: str | None = None


class RawArchive:
    """Writes published files to R2. A no-op when R2 is not configured."""

    def __init__(self) -> None:
        self._client = None
        self._enabled = settings.r2.configured
        if not self._enabled:
            log.info("R2 not configured; raw files will not be archived")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _connect(self):
        if self._client is None:
            import boto3  # imported lazily so the dependency is optional at runtime

            self._client = boto3.client(
                "s3",
                endpoint_url=settings.r2.endpoint,
                aws_access_key_id=settings.r2.access_key_id,
                aws_secret_access_key=settings.r2.secret_access_key,
                region_name="auto",
            )
        return self._client

    @staticmethod
    def key_for(chain_folder: str, file_kind: str, path: Path, when: date) -> str:
        return f"raw/{chain_folder}/{when.isoformat()}/{file_kind}/{path.name}.gz"

    def put(self, chain_folder: str, file_kind: str, path: Path, when: date) -> UploadResult:
        """Archive one file, gzipping it if the publisher did not."""
        key = self.key_for(chain_folder, file_kind, path, when)
        if not self._enabled:
            return UploadResult(key=key, uploaded=False)

        try:
            with path.open("rb") as handle:
                head = handle.read(2)
            body = path.read_bytes() if head == b"\x1f\x8b" else gzip.compress(path.read_bytes())

            self._connect().put_object(
                Bucket=settings.r2.bucket,
                Key=key,
                Body=body,
                ContentType="application/gzip",
            )
            return UploadResult(key=key, uploaded=True)
        except Exception as exc:  # noqa: BLE001 - archiving must never fail a run
            log.warning("R2 upload failed for %s: %s", path.name, exc)
            return UploadResult(key=key, uploaded=False, error=str(exc))
