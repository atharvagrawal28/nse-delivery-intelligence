"""Stock universe management: sync NIFTY50 list, backfill new symbols."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from db import cursor
from etl import is_weekend, process_date
from nse_downloader import download_nifty50_list

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def get_active_symbols() -> list[str]:
    with cursor() as cur:
        cur.execute(
            "SELECT symbol FROM stock_universe WHERE active = TRUE ORDER BY symbol"
        )
        return [row["symbol"] for row in cur.fetchall()]


def _all_symbols_with_state() -> dict[str, dict]:
    with cursor() as cur:
        cur.execute("SELECT symbol, active, added_date, removed_date FROM stock_universe")
        return {
            row["symbol"]: {
                "active": row["active"],
                "added_date": row["added_date"],
                "removed_date": row["removed_date"],
            }
            for row in cur.fetchall()
        }


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
def sync_universe() -> dict:
    """Diff NSE's current NIFTY50 list against stock_universe.

    Behavior:
      - New symbols → insert as active.
      - Symbols dropped by NSE → mark active=FALSE, set removed_date (history preserved).
      - Symbols re-added → flip active back to TRUE, clear removed_date.

    Returns counts: {"added": int, "removed": int, "reactivated": int, "unchanged": int}.
    """
    df = download_nifty50_list()
    if df is None:
        log.error("sync_universe: failed to download NIFTY50 list — aborting sync")
        return {"added": 0, "removed": 0, "reactivated": 0, "unchanged": 0}

    today = date.today()
    nse_symbols = set(df["Symbol"].tolist())
    db_state = _all_symbols_with_state()

    added: list[str] = []
    reactivated: list[str] = []
    removed: list[str] = []
    unchanged = 0

    with cursor(commit=True) as cur:
        for sym in nse_symbols:
            state = db_state.get(sym)
            if state is None:
                cur.execute(
                    "INSERT INTO stock_universe (symbol, active, added_date) "
                    "VALUES (%s, TRUE, %s)",
                    (sym, today),
                )
                added.append(sym)
            elif not state["active"]:
                cur.execute(
                    "UPDATE stock_universe SET active = TRUE, removed_date = NULL "
                    "WHERE symbol = %s",
                    (sym,),
                )
                reactivated.append(sym)
            else:
                unchanged += 1

        for sym, state in db_state.items():
            if sym not in nse_symbols and state["active"]:
                cur.execute(
                    "UPDATE stock_universe SET active = FALSE, removed_date = %s "
                    "WHERE symbol = %s",
                    (today, sym),
                )
                removed.append(sym)

    if added:
        log.info("Universe: ADDED %d symbols: %s", len(added), sorted(added))
    if reactivated:
        log.info("Universe: REACTIVATED %d symbols: %s", len(reactivated), sorted(reactivated))
    if removed:
        log.info("Universe: REMOVED %d symbols: %s", len(removed), sorted(removed))
    log.info("Universe: %d unchanged", unchanged)

    return {
        "added": len(added),
        "removed": len(removed),
        "reactivated": len(reactivated),
        "unchanged": unchanged,
        "added_symbols": added,
        "removed_symbols": removed,
        "reactivated_symbols": reactivated,
    }


# ---------------------------------------------------------------------------
# Backfill a single newly added symbol
# ---------------------------------------------------------------------------
def backfill_new_symbol(symbol: str, days: int = 365) -> dict:
    """Fetch + ingest the last `days` of bhavcopy+MTO for a single symbol.

    Wasteful (downloads each daily file) but matches the spec for handling
    universe additions. Idempotent thanks to UPSERT.
    """
    symbol = symbol.strip().upper()
    log.info("Backfilling new symbol %s (last %d days)", symbol, days)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days)

    ok = holiday = invalid = error = 0
    d = start
    while d <= end:
        if is_weekend(d):
            d += timedelta(days=1)
            continue
        try:
            res = process_date(d, symbols=[symbol])
            status = res["status"]
            if status == "ok":
                ok += 1
            elif status == "holiday":
                holiday += 1
            elif status == "invalid":
                invalid += 1
            else:
                error += 1
        except Exception as e:  # noqa: BLE001
            log.exception("backfill_new_symbol(%s) error on %s: %s", symbol, d, e)
            error += 1
        d += timedelta(days=1)

    log.info(
        "Backfill %s complete: ok=%d holiday=%d invalid=%d error=%d",
        symbol, ok, holiday, invalid, error,
    )
    return {"symbol": symbol, "ok": ok, "holiday": holiday, "invalid": invalid, "error": error}
