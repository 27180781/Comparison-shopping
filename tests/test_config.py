"""Configuration tests."""

from __future__ import annotations

import pytest

from ingestion.config import normalize_database_url


@pytest.mark.parametrize(
    "raw,expected",
    [
        # SQLAlchemy defaults postgresql:// to psycopg2, which is not installed.
        # The failure is a ModuleNotFoundError several frames deep that says
        # nothing about the URL, so a pasted URL has to keep working.
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        # Already correct, and must not be mangled.
        ("postgresql+psycopg://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        # Another dialect is somebody's deliberate choice.
        ("postgresql+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("sqlite:///local.db", "sqlite:///local.db"),
    ],
)
def test_database_url_is_pointed_at_psycopg3(raw, expected):
    assert normalize_database_url(raw) == expected


def test_only_the_scheme_is_rewritten():
    """A password containing the scheme text must survive untouched."""
    raw = "postgresql://user:postgresql://@localhost/db"
    assert normalize_database_url(raw) == "postgresql+psycopg://user:postgresql://@localhost/db"
