"""Ingestion core: chains, price groups, stores, runs, staging.

Phase 1. No PostGIS here -- geocoding is a Phase 2 concern, so the pipeline
deploys against a plain Postgres.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


# Verified against il-supermarket-scraper 1.0.8 on 2026-08-11 by
# scripts/phase0_verify_scrapers.py. Addresses live in the table, not in code,
# so a portal change is an UPDATE (ADR-006).
CHAIN_SEED = [
    # name_he, gov_chain_ids, scraper_name, fallback, portal_type, portal_url,
    # credentials_ref, is_active, notes
    ("שופרסל", ["7290027600007"], "SHUFERSAL", None, "shufersal",
     "https://prices.shufersal.co.il/", None, True, "כולל רשת BE"),
    ("רמי לוי", ["7290058140886"], "RAMI_LEVY", None, "cerberus",
     "ftp://url.retail.publishedprices.co.il", "cerberus/RamiLevi", True,
     "FTP. הפורטל דוחה AUTH TLS; ראה PHASE0-FINDINGS F-13"),
    ("קרפור ✚ יינות ביתן ✚ מגה בעיר", ["7290055700007"],
     "YAYNO_BITAN_AND_CARREFOUR", None, "publishprice",
     "https://prices.carrefour.co.il/", None, True, "אינטגרציה אחת, שלושה מותגים"),
    ("ויקטורי", ["7290696200003", "7290058103393"], "VICTORY_NEW_SOURCE", None,
     "laibcatalog_api", "https://laibcatalog.co.il", None, True,
     "המקור הישן VICTORY נמחק מהספרייה"),
    ("מחסני השוק", ["7290661400001", "7290633800006"], "MAHSANI_ASHUK_NEW_SOURCE",
     None, "laibcatalog_api", "https://laibcatalog.co.il", None, True,
     "המקור הישן MAHSANI_ASHUK נמחק מהספרייה"),
    ("אושר עד", ["7290103152017"], "OSHER_AD", None, "cerberus",
     "ftp://url.retail.publishedprices.co.il", "cerberus/osherad", True, None),
    ("מעיין 2000", ["7290058159628"], "MAAYAN_2000", None, "bina",
     "http://maayan2000.binaprojects.com/", None, True, None),
    ("נתיב החסד", ["7290058160839"], "NETIV_HASED", None, "web",
     "http://141.226.203.152/", None, False,
     "האתר מחזיר HTTP 500 מאז 2026-07-24. כולל ברכל. F-5"),
    ("שפע ברכת השם", ["7290058134977"], "SHEFA_BARCART_ASHEM", None, "bina",
     "http://shefabirkathashem.binaprojects.com/", None, True, None),
    ("זול ובגדול", ["7290058173198"], "ZOL_VEBEGADOL", None, "bina",
     "http://zolvebegadol.binaprojects.com/", None, True, None),
    ("שוק העיר", ["7290058148776"], "SHUK_AHIR", None, "bina",
     "http://shuk-hayir.binaprojects.com/", None, True, None),
    ("קייטי / משנת יוסף", ["5144744100002"], "MESHMAT_YOSEF_1", None, "web",
     "https://list-files.w5871031-kt.workers.dev/", None, True,
     "לא מפרסם מבצעים. F-6"),
    ("קייטי / משנת יוסף", ["5144744100001", "7290058289400", "2222222"],
     "MESHMAT_YOSEF_2", None, "bina", "http://ktshivuk.binaprojects.com/", None,
     True, None),
]


def upgrade() -> None:
    op.create_table(
        "chains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name_he", sa.Text(), nullable=False),
        # Several scrapers report more than one gov chain id (F-7).
        sa.Column("gov_chain_ids", postgresql.ARRAY(sa.Text())),
        sa.Column("scraper_name", sa.Text(), nullable=False, unique=True),
        sa.Column("scraper_fallback", sa.Text()),
        sa.Column("portal_type", sa.Text(), nullable=False),
        sa.Column("portal_url", sa.Text()),
        # A key into the secret store, never the secret itself.
        sa.Column("credentials_ref", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text()),
    )

    op.create_table(
        "price_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain_id", sa.Integer(), sa.ForeignKey("chains.id"), nullable=False),
        sa.Column("sub_chain_code", sa.Text(), nullable=False),
        sa.Column("label", sa.Text()),
        sa.UniqueConstraint("chain_id", "sub_chain_code"),
    )

    op.create_table(
        "stores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain_id", sa.Integer(), sa.ForeignKey("chains.id"), nullable=False),
        sa.Column("price_group_id", sa.Integer(), sa.ForeignKey("price_groups.id")),
        sa.Column("store_code", sa.Text(), nullable=False),
        sa.Column("name_he", sa.Text()),
        sa.Column("address", sa.Text()),
        sa.Column("city", sa.Text()),
        sa.Column("zip_code", sa.Text()),
        sa.Column("store_type", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("chain_id", "store_code"),
    )

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("chain_id", sa.Integer(), sa.ForeignKey("chains.id")),
        sa.Column("scraper_name", sa.Text()),
        sa.Column("file_types", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("row_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_downloaded", sa.BigInteger(), nullable=False, server_default="0"),
        # skipped_unstable distinguishes "the library disabled this chain" from
        # "the portal returned nothing" -- otherwise both look like success (F-5).
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text()),
    )
    op.create_index(
        "idx_runs_chain_started",
        "ingestion_runs",
        ["chain_id", sa.text("started_at DESC")],
    )

    op.create_table(
        "staging_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("run_id", sa.BigInteger()),
        sa.Column("chain_id", sa.Integer()),
        sa.Column("sub_chain_code", sa.Text()),
        sa.Column("store_code", sa.Text()),
        sa.Column("item_code", sa.Text()),
        sa.Column("barcode", sa.Text()),
        sa.Column("item_type", sa.SmallInteger()),
        sa.Column("raw_name_he", sa.Text()),
        sa.Column("manufacturer", sa.Text()),
        sa.Column("unit_qty", sa.Text()),
        sa.Column("quantity", sa.Numeric(12, 3)),
        sa.Column("unit_of_measure", sa.Text()),
        sa.Column("is_weighted", sa.Boolean()),
        # NUMERIC, never float. Missing price is NULL, never 0.
        sa.Column("price", sa.Numeric(10, 2)),
        sa.Column("unit_price", sa.Numeric(10, 4)),
        sa.Column("price_updated_at", sa.DateTime(timezone=True)),
        sa.Column("file_date", sa.Date()),
        sa.Column("source_key", sa.String(512)),
    )
    op.create_index("idx_staging_run", "staging_items", ["run_id"])
    op.create_index("idx_staging_barcode", "staging_items", ["barcode"])

    chains = sa.table(
        "chains",
        sa.column("name_he", sa.Text),
        sa.column("gov_chain_ids", postgresql.ARRAY(sa.Text)),
        sa.column("scraper_name", sa.Text),
        sa.column("scraper_fallback", sa.Text),
        sa.column("portal_type", sa.Text),
        sa.column("portal_url", sa.Text),
        sa.column("credentials_ref", sa.Text),
        sa.column("is_active", sa.Boolean),
        sa.column("notes", sa.Text),
    )
    op.bulk_insert(
        chains,
        [
            {
                "name_he": row[0],
                "gov_chain_ids": row[1],
                "scraper_name": row[2],
                "scraper_fallback": row[3],
                "portal_type": row[4],
                "portal_url": row[5],
                "credentials_ref": row[6],
                "is_active": row[7],
                "notes": row[8],
            }
            for row in CHAIN_SEED
        ],
    )


def downgrade() -> None:
    op.drop_table("staging_items")
    op.drop_table("ingestion_runs")
    op.drop_table("stores")
    op.drop_table("price_groups")
    op.drop_table("chains")
