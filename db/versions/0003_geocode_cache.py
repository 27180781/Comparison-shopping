"""Geocoding cache and review queue.

Phase 2. Addresses repeat across chains and across runs, and every lookup is
billed, so resolved queries are kept. The cache is keyed on the normalised
query rather than the store, which is what makes two chains listing the same
shopping centre cost one lookup.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "geocode_cache",
        sa.Column("query", sa.Text(), primary_key=True),
        sa.Column("lat", sa.Float()),
        sa.Column("lng", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("provider", sa.Text()),
        # What the provider echoed back. Kept because a wrong geocode is
        # usually obvious from the formatted address and invisible from the
        # coordinates.
        sa.Column("formatted_address", sa.Text()),
        sa.Column("location_type", sa.Text()),
        sa.Column("partial_match", sa.Boolean(), nullable=False, server_default=sa.false()),
        # Set when a result was returned but refused - outside Israel, or a
        # city centre standing in for a street address. Kept so a re-run does
        # not pay to be told the same thing again.
        sa.Column("rejected_reason", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.add_column("stores", sa.Column("geocode_query", sa.Text()))
    op.add_column(
        "stores",
        sa.Column("geocode_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # The manual review queue is a partial index rather than a table: the queue
    # is a view of stores, and a separate table would drift from it.
    op.create_index(
        "idx_stores_needs_review",
        "stores",
        ["chain_id", "geocode_confidence"],
        postgresql_where=sa.text("geocode_verified = false"),
    )


def downgrade() -> None:
    op.drop_index("idx_stores_needs_review", table_name="stores")
    op.drop_column("stores", "geocode_verified")
    op.drop_column("stores", "geocode_query")
    op.drop_table("geocode_cache")
