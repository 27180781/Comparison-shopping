"""Price history as SCD Type 2, plus the flat table the API reads.

The model, measured rather than assumed: within a price group almost every
store charges the same, so a price is stored once per group and only the
disagreements are stored per store. Phase 0 measurement #2 put those
disagreements at 6.59%, against the 15% that would have sunk the idea.

    effective price = COALESCE(exception, base)

History is the real asset. Because every change closes a row and opens a new
one, the database can answer the question no shopping app answers: was this
promotion ever actually a discount, or has the price been this all year?

`price_current` is a flattened copy so the read path never touches history.
It is rebuilt after each cycle rather than maintained incrementally -- at this
volume a rebuild is seconds, and a rebuild cannot drift.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


@dataclass
class PriceReport:
    base_opened: int = 0
    base_closed: int = 0
    exceptions_opened: int = 0
    exceptions_closed: int = 0
    current_rows: int = 0

    def as_dict(self) -> dict[str, int]:
        return self.__dict__.copy()


# The price a group agrees on: the most common price among its stores, ties
# broken by the lower price so a split never invents an increase.
MODAL_PRICE_SQL = """
WITH staged AS (
    SELECT s.chain_id,
           pg.id           AS price_group_id,
           v.id            AS variant_id,
           st.id           AS store_id,
           s.price,
           s.unit_price
      FROM staging_items s
      JOIN product_variants v
        ON v.chain_id = s.chain_id AND v.item_code = s.item_code
      JOIN stores st
        ON st.chain_id = s.chain_id AND st.store_code = s.store_code
      LEFT JOIN price_groups pg
        ON pg.chain_id = s.chain_id AND pg.sub_chain_code = s.sub_chain_code
     WHERE s.price IS NOT NULL
       AND s.item_code IS NOT NULL
),
ranked AS (
    SELECT price_group_id,
           variant_id,
           price,
           MIN(unit_price) AS unit_price,
           COUNT(*)        AS store_count,
           ROW_NUMBER() OVER (
               PARTITION BY price_group_id, variant_id
               ORDER BY COUNT(*) DESC, price ASC
           ) AS rank
      FROM staged
     WHERE price_group_id IS NOT NULL
     GROUP BY price_group_id, variant_id, price
)
SELECT price_group_id, variant_id, price, unit_price
  FROM ranked
 WHERE rank = 1
"""


def rebuild(session: Session, now: datetime | None = None) -> PriceReport:
    """Fold the current staging contents into history, then flatten."""
    now = now or datetime.now(timezone.utc)
    report = PriceReport()

    session.execute(text("CREATE TEMP TABLE tmp_base ON COMMIT DROP AS " + MODAL_PRICE_SQL))
    session.execute(text("CREATE INDEX ON tmp_base (price_group_id, variant_id)"))

    report.base_closed = _close_changed_base(session, now)
    report.base_opened = _open_base(session, now)

    session.execute(text("CREATE TEMP TABLE tmp_exception ON COMMIT DROP AS " + EXCEPTION_SQL))
    session.execute(text("CREATE INDEX ON tmp_exception (store_id, variant_id)"))

    report.exceptions_closed = _close_changed_exceptions(session, now)
    report.exceptions_opened = _open_exceptions(session, now)

    report.current_rows = _rebuild_current(session, now)
    _roll_up_daily(session)
    _warn_if_model_broke(report)
    return report


# ADR-002 states the threshold plainly: above this share of exceptions, storing
# a base price per group stops paying for itself.
EXCEPTION_ALARM_PCT = 15


def _warn_if_model_broke(report: PriceReport) -> None:
    """Say out loud when base+exceptions stops holding.

    The usual cause is not that prices diverged but that the price group lookup
    missed -- a store or sub-chain code that failed to join. That produces a
    perfectly successful run in which every price is an exception, so without
    this the model can collapse with nothing in the log to show it.
    """
    written = report.base_opened + report.exceptions_opened
    if written < 100:
        return

    share = report.exceptions_opened / written * 100
    if share <= EXCEPTION_ALARM_PCT:
        return

    log.warning(
        "%.1f%% of prices were written as exceptions (%s of %s), against the %s%% "
        "ceiling in ADR-002. Phase 0 measured 6.59%%. Check that staging_items "
        "sub_chain_code and store_code join price_groups and stores -- a missed "
        "join sends everything here.",
        share,
        report.exceptions_opened,
        written,
        EXCEPTION_ALARM_PCT,
    )


# A store is an exception only when it disagrees with its group's price. Stores
# with no price group at all are exceptions by definition, since there is
# nothing for them to agree with.
EXCEPTION_SQL = """
WITH staged AS (
    SELECT s.chain_id,
           pg.id AS price_group_id,
           v.id  AS variant_id,
           st.id AS store_id,
           s.price,
           s.unit_price
      FROM staging_items s
      JOIN product_variants v
        ON v.chain_id = s.chain_id AND v.item_code = s.item_code
      JOIN stores st
        ON st.chain_id = s.chain_id AND st.store_code = s.store_code
      LEFT JOIN price_groups pg
        ON pg.chain_id = s.chain_id AND pg.sub_chain_code = s.sub_chain_code
     WHERE s.price IS NOT NULL
       AND s.item_code IS NOT NULL
)
SELECT DISTINCT ON (staged.store_id, staged.variant_id)
       staged.store_id,
       staged.variant_id,
       staged.price,
       staged.unit_price
  FROM staged
  LEFT JOIN tmp_base b
    ON b.price_group_id = staged.price_group_id
   AND b.variant_id = staged.variant_id
 WHERE b.price IS NULL OR b.price <> staged.price
 ORDER BY staged.store_id, staged.variant_id, staged.price ASC
"""


def _close_changed_base(session: Session, now: datetime) -> int:
    return session.execute(
        text(
            """
            UPDATE price_base pb
               SET valid_to = :now
              FROM tmp_base t
             WHERE pb.price_group_id = t.price_group_id
               AND pb.variant_id = t.variant_id
               AND pb.valid_to IS NULL
               AND pb.price IS DISTINCT FROM t.price
            """
        ),
        {"now": now},
    ).rowcount or 0


def _open_base(session: Session, now: datetime) -> int:
    return session.execute(
        text(
            """
            INSERT INTO price_base (price_group_id, variant_id, price, unit_price, valid_from)
            SELECT t.price_group_id, t.variant_id, t.price, t.unit_price, :now
              FROM tmp_base t
             WHERE NOT EXISTS (
                   SELECT 1 FROM price_base pb
                    WHERE pb.price_group_id = t.price_group_id
                      AND pb.variant_id = t.variant_id
                      AND pb.valid_to IS NULL
             )
            """
        ),
        {"now": now},
    ).rowcount or 0


def _close_changed_exceptions(session: Session, now: datetime) -> int:
    """Close exceptions that changed, and those that stopped being exceptions."""
    changed = session.execute(
        text(
            """
            UPDATE price_exception pe
               SET valid_to = :now
              FROM tmp_exception t
             WHERE pe.store_id = t.store_id
               AND pe.variant_id = t.variant_id
               AND pe.valid_to IS NULL
               AND pe.price IS DISTINCT FROM t.price
            """
        ),
        {"now": now},
    ).rowcount or 0

    resolved = session.execute(
        text(
            """
            UPDATE price_exception pe
               SET valid_to = :now
             WHERE pe.valid_to IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM tmp_exception t
                    WHERE t.store_id = pe.store_id AND t.variant_id = pe.variant_id
               )
               AND EXISTS (
                   SELECT 1 FROM staging_items s
                     JOIN stores st ON st.chain_id = s.chain_id
                                   AND st.store_code = s.store_code
                    WHERE st.id = pe.store_id
               )
            """
        ),
        {"now": now},
    ).rowcount or 0

    return changed + resolved


def _open_exceptions(session: Session, now: datetime) -> int:
    return session.execute(
        text(
            """
            INSERT INTO price_exception (store_id, variant_id, price, unit_price, valid_from)
            SELECT t.store_id, t.variant_id, t.price, t.unit_price, :now
              FROM tmp_exception t
             WHERE NOT EXISTS (
                   SELECT 1 FROM price_exception pe
                    WHERE pe.store_id = t.store_id
                      AND pe.variant_id = t.variant_id
                      AND pe.valid_to IS NULL
             )
            """
        ),
        {"now": now},
    ).rowcount or 0


def _rebuild_current(session: Session, now: datetime) -> int:
    """Flatten open history into one row per (store, variant).

    Only confidently matched variants get a canonical_id, so an uncertain match
    can never be summed into a basket by accident (ADR-010). The row still
    exists -- the product is visible, it is just not comparable.

    normalized_unit_price is computed here rather than at read time so sorting
    by price per 100g is an index scan rather than a per-row calculation.
    """
    session.execute(text("TRUNCATE price_current"))
    return session.execute(
        text(
            """
            INSERT INTO price_current
                (store_id, variant_id, canonical_id, price, normalized_unit_price, updated_at)
            SELECT st.id,
                   v.id,
                   v.canonical_id,
                   COALESCE(pe.price, pb.price),
                   CASE
                     WHEN cp.unit_of_measure IN ('g', 'ml')
                          AND COALESCE(cp.base_size, 0) > 0
                       THEN ROUND(
                            COALESCE(pe.price, pb.price) / cp.base_size * 100, 4)
                     WHEN cp.id IS NOT NULL
                       THEN ROUND(
                            COALESCE(pe.price, pb.price) / GREATEST(cp.pack_count, 1), 4)
                     ELSE NULL
                   END,
                   :now
              FROM stores st
              JOIN product_variants v ON v.chain_id = st.chain_id
              LEFT JOIN price_groups pg ON pg.id = st.price_group_id
              LEFT JOIN price_base pb
                     ON pb.price_group_id = pg.id
                    AND pb.variant_id = v.id
                    AND pb.valid_to IS NULL
              LEFT JOIN price_exception pe
                     ON pe.store_id = st.id
                    AND pe.variant_id = v.id
                    AND pe.valid_to IS NULL
              LEFT JOIN canonical_products cp ON cp.id = v.canonical_id
             WHERE COALESCE(pe.price, pb.price) IS NOT NULL
            """
        ),
        {"now": now},
    ).rowcount or 0


def _roll_up_daily(session: Session) -> None:
    """Daily min/max/avg per product, so the 90-day chart is one indexed read."""
    session.execute(
        text(
            """
            INSERT INTO price_daily (canonical_id, day, min_price, max_price, avg_price, store_count)
            SELECT canonical_id,
                   CURRENT_DATE,
                   MIN(price),
                   MAX(price),
                   ROUND(AVG(price), 2),
                   COUNT(DISTINCT store_id)
              FROM price_current
             WHERE canonical_id IS NOT NULL AND price IS NOT NULL
             GROUP BY canonical_id
            ON CONFLICT (canonical_id, day) DO UPDATE
               SET min_price = EXCLUDED.min_price,
                   max_price = EXCLUDED.max_price,
                   avg_price = EXCLUDED.avg_price,
                   store_count = EXCLUDED.store_count
            """
        )
    )
