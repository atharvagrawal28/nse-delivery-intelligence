"""PostgreSQL connection helpers + schema initialization.

Run directly to (re-)create the schema:
    python db.py
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from config import DATABASE_URL, configure_logging

log = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stock_universe (
    symbol         TEXT        PRIMARY KEY,
    active         BOOLEAN     NOT NULL DEFAULT TRUE,
    added_date     DATE        NOT NULL,
    removed_date   DATE
);

CREATE TABLE IF NOT EXISTS delivery_history (
    id               BIGSERIAL    PRIMARY KEY,
    symbol           TEXT         NOT NULL,
    series           TEXT         NOT NULL,
    date             DATE         NOT NULL,
    traded_qty       BIGINT       NOT NULL,
    turnover         NUMERIC(20,2) NOT NULL,
    trades           BIGINT       NOT NULL,
    deliverable_qty  BIGINT       NOT NULL,
    delivery_pct     NUMERIC(7,2) NOT NULL,
    close_price      NUMERIC(14,2) NOT NULL,
    CONSTRAINT delivery_history_unique UNIQUE (symbol, series, date)
);

CREATE INDEX IF NOT EXISTS idx_delivery_symbol_date
    ON delivery_history (symbol, date DESC);

CREATE INDEX IF NOT EXISTS idx_delivery_date
    ON delivery_history (date);
"""


def get_conn():
    """Open a new psycopg2 connection. Caller is responsible for closing."""
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to .env (local) or Streamlit/GitHub secrets."
        )
    return psycopg2.connect(DATABASE_URL)


@contextmanager
def cursor(commit: bool = False):
    """Yield a DictCursor; commit on clean exit if requested."""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        try:
            yield cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    finally:
        conn.close()


def init_schema() -> None:
    log.info("Initializing schema ...")
    with cursor(commit=True) as cur:
        cur.execute(SCHEMA_SQL)
    log.info("Schema ready.")


def upsert_delivery_rows(rows: list[tuple]) -> int:
    """Bulk upsert delivery_history rows.

    Each row tuple must be:
        (symbol, series, date, traded_qty, turnover, trades,
         deliverable_qty, delivery_pct, close_price)
    Returns the number of rows the server reports as affected.
    """
    if not rows:
        return 0
    sql = """
        INSERT INTO delivery_history
            (symbol, series, date, traded_qty, turnover, trades,
             deliverable_qty, delivery_pct, close_price)
        VALUES %s
        ON CONFLICT (symbol, series, date) DO UPDATE SET
            traded_qty      = EXCLUDED.traded_qty,
            turnover        = EXCLUDED.turnover,
            trades          = EXCLUDED.trades,
            deliverable_qty = EXCLUDED.deliverable_qty,
            delivery_pct    = EXCLUDED.delivery_pct,
            close_price     = EXCLUDED.close_price
    """
    with cursor(commit=True) as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=500)
        affected = cur.rowcount
    return affected


def date_already_loaded(date) -> bool:
    """Return True if delivery_history already has any row for `date`."""
    with cursor() as cur:
        cur.execute("SELECT 1 FROM delivery_history WHERE date = %s LIMIT 1", (date,))
        return cur.fetchone() is not None


if __name__ == "__main__":
    configure_logging()
    init_schema()
