"""Remember which published files are already in the database.

Retailers republish the same full snapshot several times a day under the same
name, and the scraper keeps no memory across runs. Without this table every
cycle re-parses everything it already holds; with it, a cycle opens only files
it has never seen and one that finds nothing new finishes in seconds.

Keyed on (chain_id, file_name) with the byte count alongside, so a chain that
republishes a corrected file under an unchanged name is picked up rather than
skipped.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingested_files",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(length=512), nullable=False),
        sa.Column("kind", sa.Text(), nullable=True),
        sa.Column("file_date", sa.Date(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("row_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("source_key", sa.String(length=512), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["chain_id"], ["chains.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chain_id", "file_name"),
    )
    op.create_index(
        "idx_ingested_files_chain_date",
        "ingested_files",
        ["chain_id", sa.text("file_date DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_ingested_files_chain_date", table_name="ingested_files")
    op.drop_table("ingested_files")
