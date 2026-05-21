"""ETL pipeline: merge bhavcopy + MTO, validate, upsert.

Public entry point:
    process_date(d, symbols) -> dict with counts + validation result.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Iterable, Optional

import pandas as pd

from db import upsert_delivery_rows
from nse_downloader import download_bhavcopy, download_mto

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------
def _merge_bhav_mto(
    bhav: pd.DataFrame,
    mto: pd.DataFrame,
    d: date,
    symbols: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Return one merged DataFrame keyed by (symbol, series) for a single date."""
    b = bhav[["SYMBOL", "SERIES", "TOTTRDQTY", "TOTTRDVAL", "TOTALTRADES", "CLOSE"]].copy()
    b["SYMBOL"] = b["SYMBOL"].astype(str).str.strip().str.upper()
    b["SERIES"] = b["SERIES"].astype(str).str.strip().str.upper()
    b = b.rename(columns={
        "SYMBOL": "symbol",
        "SERIES": "series",
        "TOTTRDQTY": "traded_qty",
        "TOTTRDVAL": "turnover",
        "TOTALTRADES": "trades",
        "CLOSE": "close_price",
    })

    m = mto[["symbol", "series", "deliverable_qty", "delivery_pct"]].copy()
    m["symbol"] = m["symbol"].astype(str).str.strip().str.upper()
    m["series"] = m["series"].astype(str).str.strip().str.upper()

    merged = b.merge(m, on=["symbol", "series"], how="inner")
    if symbols is not None:
        wanted = {s.strip().upper() for s in symbols}
        merged = merged[merged["symbol"].isin(wanted)]

    merged["date"] = d
    return merged[[
        "symbol", "series", "date",
        "traded_qty", "turnover", "trades",
        "deliverable_qty", "delivery_pct", "close_price",
    ]]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
REQUIRED_COLUMNS = {
    "symbol", "series", "date",
    "traded_qty", "turnover", "trades",
    "deliverable_qty", "delivery_pct", "close_price",
}


def validate(df: pd.DataFrame) -> dict:
    """Return {'valid': bool, 'warnings': [...], 'errors': [...]}.

    Errors halt the insert; warnings are logged but allowed through.
    """
    warnings: list[str] = []
    errors: list[str] = []

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        errors.append(f"missing required columns: {sorted(missing)}")
        return {"valid": False, "warnings": warnings, "errors": errors}

    if len(df) == 0:
        errors.append("empty dataframe")
        return {"valid": False, "warnings": warnings, "errors": errors}

    dup_mask = df.duplicated(subset=["symbol", "date"], keep=False)
    if dup_mask.any():
        dup_keys = (
            df.loc[dup_mask, ["symbol", "date"]]
              .drop_duplicates()
              .head(5)
              .to_dict("records")
        )
        warnings.append(f"{int(dup_mask.sum())} duplicate (symbol,date) rows (sample: {dup_keys})")

    neg_pct = (df["delivery_pct"] < 0).sum()
    if neg_pct:
        errors.append(f"{int(neg_pct)} rows with negative delivery_pct")
    over_pct = (df["delivery_pct"] > 100).sum()
    if over_pct:
        warnings.append(f"{int(over_pct)} rows with delivery_pct > 100 (likely NSE rounding)")

    neg_traded = (df["traded_qty"] < 0).sum()
    if neg_traded:
        errors.append(f"{int(neg_traded)} rows with negative traded_qty")
    neg_deliv = (df["deliverable_qty"] < 0).sum()
    if neg_deliv:
        errors.append(f"{int(neg_deliv)} rows with negative deliverable_qty")

    deliv_gt_traded = (df["deliverable_qty"] > df["traded_qty"]).sum()
    if deliv_gt_traded:
        warnings.append(
            f"{int(deliv_gt_traded)} rows where deliverable_qty > traded_qty"
        )

    return {"valid": not errors, "warnings": warnings, "errors": errors}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def process_date(
    d: date,
    symbols: Optional[Iterable[str]] = None,
) -> dict:
    """Download → merge → validate → upsert for a single trading date.

    Returns dict:
        {"date": d, "status": "ok|holiday|partial|invalid|error",
         "rows": int, "validation": {...}}
    """
    log.info("ETL start: date=%s symbols=%s", d, "ALL" if symbols is None else f"{len(list(symbols))} symbols")
    # Re-materialize symbols (list above consumed the iterator).
    symbol_list = None if symbols is None else list(symbols)

    bhav = download_bhavcopy(d)
    mto = download_mto(d)

    if bhav is None and mto is None:
        log.info("ETL %s: no files available — treating as holiday", d)
        return {"date": d, "status": "holiday", "rows": 0, "validation": None}

    if bhav is None or mto is None:
        log.warning("ETL %s: partial download (bhav=%s, mto=%s)",
                    d, bhav is not None, mto is not None)
        return {"date": d, "status": "partial", "rows": 0, "validation": None}

    merged = _merge_bhav_mto(bhav, mto, d, symbol_list)
    log.info("ETL %s: merged rows=%d", d, len(merged))

    result = validate(merged)
    for w in result["warnings"]:
        log.warning("validate(%s) WARNING: %s", d, w)
    for e in result["errors"]:
        log.error("validate(%s) ERROR: %s", d, e)

    if not result["valid"]:
        log.error("ETL %s: validation failed — skipping insert", d)
        return {"date": d, "status": "invalid", "rows": 0, "validation": result}

    rows = list(merged.itertuples(index=False, name=None))
    affected = upsert_delivery_rows(rows)
    log.info("ETL %s: upserted %d rows", d, affected)
    return {"date": d, "status": "ok", "rows": affected, "validation": result}


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
def is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # Sat=5, Sun=6


def previous_trading_day(today: Optional[date] = None) -> date:
    """Return the most recent weekday strictly before `today`.

    Does NOT account for NSE holidays — those are absorbed by the 404 handler
    in the downloader, which marks the day as 'holiday'.
    """
    if today is None:
        today = datetime.now().date()
    d = today
    while True:
        d = d.fromordinal(d.toordinal() - 1)
        if not is_weekend(d):
            return d
