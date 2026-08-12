"""Correct the Cerberus note seeded in 0001.

0001 recorded that the Cerberus portal refuses AUTH TLS. It does not: the
server advertises AUTH SSL and AUTH TLS, accepts either, and lists 2249 entries
over FTPS. The 504 that produced that note came from a middlebox on one
network, not from the portal.

Applied as a data migration rather than by editing 0001, since that revision
has already run against existing databases.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

CORRECTED = "FTP over TLS. משתמש מקודד בספרייה, סיסמה ריקה"
ORIGINAL = "FTP. הפורטל דוחה AUTH TLS; ראה PHASE0-FINDINGS F-13"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE chains SET notes = :corrected "
            "WHERE scraper_name = 'RAMI_LEVY' AND notes = :original"
        ).bindparams(corrected=CORRECTED, original=ORIGINAL)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE chains SET notes = :original "
            "WHERE scraper_name = 'RAMI_LEVY' AND notes = :corrected"
        ).bindparams(corrected=CORRECTED, original=ORIGINAL)
    )
