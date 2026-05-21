"""Backfill last N days of bhavcopy+MTO for all active NIFTY50 symbols.

Idempotent — re-running skips dates already present (and the UPSERT in
db.upsert_delivery_rows safely overwrites if you want to force-refresh).
"""
from __future__ import annotations

import logging
import sys
from datetime import date, timedelta

from config import BACKFILL_CHUNK_DAYS, BACKFILL_DAYS, configure_logging
from db import date_already_loaded, init_schema
from etl import is_weekend, process_date
from universe import get_active_symbols, sync_universe

log = logging.getLogger(__name__)


def _chunked_ranges(start: date, end: date, chunk_days: int):
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=chunk_days - 1), end)
        yield cur, nxt
        cur = nxt + timedelta(days=1)


def run_backfill(days: int = BACKFILL_DAYS, force: bool = False) -> dict:
    """Backfill last `days` calendar days for all active symbols.

    `force=True` re-downloads even dates already present in the DB.
    """
    init_schema()
    sync_universe()

    symbols = get_active_symbols()
    if not symbols:
        log.error("No active symbols in stock_universe — aborting backfill")
        return {"ok": 0, "holiday": 0, "skipped": 0, "invalid": 0, "error": 0}

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    log.info("Backfill window: %s .. %s (%d days, %d symbols)",
             start, end, days, len(symbols))

    totals = {"ok": 0, "holiday": 0, "skipped": 0, "invalid": 0, "error": 0, "rows": 0}

    for chunk_start, chunk_end in _chunked_ranges(start, end, BACKFILL_CHUNK_DAYS):
        log.info("---- chunk %s .. %s ----", chunk_start, chunk_end)
        d = chunk_start
        while d <= chunk_end:
            if is_weekend(d):
                d += timedelta(days=1)
                continue
            if not force and date_already_loaded(d):
                log.info("Skip %s: already loaded", d)
                totals["skipped"] += 1
                d += timedelta(days=1)
                continue
            try:
                res = process_date(d, symbols=symbols)
            except Exception as e:  # noqa: BLE001
                log.exception("process_date(%s) raised: %s", d, e)
                totals["error"] += 1
                d += timedelta(days=1)
                continue
            status = res["status"]
            if status == "ok":
                totals["ok"] += 1
                totals["rows"] += res["rows"]
            elif status == "holiday":
                totals["holiday"] += 1
            elif status == "invalid":
                totals["invalid"] += 1
            else:
                totals["error"] += 1
            d += timedelta(days=1)

    log.info(
        "Backfill complete: ok=%d holiday=%d skipped=%d invalid=%d error=%d rows=%d",
        totals["ok"], totals["holiday"], totals["skipped"],
        totals["invalid"], totals["error"], totals["rows"],
    )
    return totals


if __name__ == "__main__":
    configure_logging()
    force = "--force" in sys.argv
    days = BACKFILL_DAYS
    for a in sys.argv[1:]:
        if a.startswith("--days="):
            days = int(a.split("=", 1)[1])
    totals = run_backfill(days=days, force=force)
    # exit non-zero only if we got nothing at all
    if totals["ok"] == 0 and totals["skipped"] == 0:
        sys.exit(1)
