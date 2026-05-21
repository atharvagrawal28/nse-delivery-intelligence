"""Analytics computed in Python (not SQL).

Read raw rows out of Postgres, decorate with derived columns, return DataFrame.
All computations are deterministic and reproducible from the same input.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from config import (
    PERCENTILE_WINDOW_SESSIONS,
    ROLLING_20D,
    ROLLING_5D,
    SIGNAL_THRESHOLDS,
)
from db import cursor

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def load_history(
    symbols: Optional[list[str]] = None,
    start_date=None,
    end_date=None,
) -> pd.DataFrame:
    """Load delivery_history rows. All filters are optional."""
    conds = []
    params: list = []
    if symbols:
        conds.append("symbol = ANY(%s)")
        params.append(list(symbols))
    if start_date:
        conds.append("date >= %s")
        params.append(start_date)
    if end_date:
        conds.append("date <= %s")
        params.append(end_date)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    sql = f"""
        SELECT symbol, series, date,
               traded_qty, turnover, trades,
               deliverable_qty, delivery_pct, close_price
        FROM delivery_history
        {where}
        ORDER BY symbol, date
    """
    with cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=[
            "symbol", "series", "date",
            "traded_qty", "turnover", "trades",
            "deliverable_qty", "delivery_pct", "close_price",
        ])
    df = pd.DataFrame([dict(r) for r in rows])
    # NUMERIC → Decimal from psycopg2; cast to float for math.
    for c in ("turnover", "delivery_pct", "close_price"):
        df[c] = df[c].astype(float)
    for c in ("traded_qty", "trades", "deliverable_qty"):
        df[c] = df[c].astype("int64")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


# ---------------------------------------------------------------------------
# Derived columns
# ---------------------------------------------------------------------------
def _dod_pct(s: pd.Series) -> pd.Series:
    prev = s.shift(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (s - prev) / prev * 100.0
    return out.replace([np.inf, -np.inf], np.nan)


def _rolling_percentile(s: pd.Series, window: int) -> pd.Series:
    """For each row, % of the prior `window` values that are <= the current value.

    Trailing window only (uses the current value itself, ranking it among the
    last `window` sessions including today — gives a value in [0, 100]).
    """
    def rank(arr: np.ndarray) -> float:
        cur = arr[-1]
        return float((arr <= cur).sum()) / len(arr) * 100.0
    return s.rolling(window=window, min_periods=2).apply(rank, raw=True)


def _classify(row) -> str:
    pct = row["delivery_pct"]
    avg20 = row["delivery_pct_20d_avg"]
    p = row["delivery_pct_percentile_1y"]
    dod = row["dod_deliverable_qty"]

    if pd.isna(pct) or pd.isna(avg20) or pd.isna(p):
        return "Neutral"

    t = SIGNAL_THRESHOLDS
    if (pct > avg20 * t["accumulation"]["avg_mult"]
            and p >= t["accumulation"]["percentile_min"]
            and not pd.isna(dod) and dod >= t["accumulation"]["dod_min"]):
        return "Accumulation"
    if (pct > avg20 * t["distribution"]["avg_mult"]
            and p >= t["distribution"]["percentile_min"]
            and not pd.isna(dod) and dod < t["distribution"]["dod_max"]):
        return "Distribution"
    if (pct > avg20 * t["elevated"]["avg_mult"]
            and p >= t["elevated"]["percentile_min"]):
        return "Elevated"
    if (pct < avg20 * t["thin"]["avg_mult"]
            and p <= t["thin"]["percentile_max"]):
        return "Thin/Speculative"
    return "Neutral"


def decorate(df: pd.DataFrame) -> pd.DataFrame:
    """Add DoD %, rolling averages, 1y percentile, and Signal columns.

    Per-symbol: rows must already be sorted by date ASC (loader guarantees).
    """
    if df.empty:
        for col in (
            "dod_traded_qty", "dod_turnover", "dod_deliverable_qty",
            "delivery_pct_5d_avg", "delivery_pct_20d_avg",
            "delivery_pct_percentile_1y", "signal",
        ):
            df[col] = pd.Series(dtype="float64")
        df["signal"] = pd.Series(dtype="object")
        return df

    log.info("analytics: decorating %d rows across %d symbols",
             len(df), df["symbol"].nunique())

    grouped = df.groupby("symbol", sort=False, group_keys=False)
    df["dod_traded_qty"]      = grouped["traded_qty"].transform(_dod_pct)
    df["dod_turnover"]        = grouped["turnover"].transform(_dod_pct)
    df["dod_deliverable_qty"] = grouped["deliverable_qty"].transform(_dod_pct)

    df["delivery_pct_5d_avg"] = grouped["delivery_pct"].transform(
        lambda s: s.rolling(ROLLING_5D, min_periods=1).mean()
    )
    df["delivery_pct_20d_avg"] = grouped["delivery_pct"].transform(
        lambda s: s.rolling(ROLLING_20D, min_periods=1).mean()
    )
    df["delivery_pct_percentile_1y"] = grouped["delivery_pct"].transform(
        lambda s: _rolling_percentile(s, PERCENTILE_WINDOW_SESSIONS)
    )

    df["signal"] = df.apply(_classify, axis=1)
    return df


def latest_per_symbol(df: pd.DataFrame) -> pd.DataFrame:
    """Return the most recent row per symbol."""
    if df.empty:
        return df
    return df.sort_values("date").groupby("symbol", as_index=False).tail(1)
