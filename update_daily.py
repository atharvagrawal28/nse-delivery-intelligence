"""Daily updater — runs from cron / GitHub Actions every weekday evening (IST).

Sequence:
    1. clean /data/raw/ folders older than RAW_RETENTION_DAYS
    2. sync NIFTY50 universe (add/remove)
    3. backfill any newly added symbols (last 365d)
    4. determine the previous trading day (skip weekends; holidays handled
       by the 404 path in the downloader)
    5. skip if that date is already loaded
    6. download → validate → ETL → upsert
    7. exit 1 on hard failure so GitHub Actions can alert
"""
from __future__ import annotations

import logging
import sys

from config import configure_logging
from db import date_already_loaded, init_schema
from etl import is_weekend, previous_trading_day, process_date
from nse_downloader import clean_old_raw_folders
from universe import backfill_new_symbol, get_active_symbols, sync_universe

log = logging.getLogger(__name__)


def main() -> int:
    configure_logging()
    log.info("==== Daily update start ====")

    # 1. Housekeeping
    clean_old_raw_folders()

    # 2. Schema must exist
    init_schema()

    # 3. Universe sync + per-new-symbol backfill
    sync_result = sync_universe()
    for sym in sync_result.get("added_symbols", []):
        log.info("New universe member %s — running 365d backfill", sym)
        backfill_new_symbol(sym)

    symbols = get_active_symbols()
    if not symbols:
        log.error("No active symbols — aborting")
        return 1

    # 4. Target date
    # On a weekday the script runs at ~6:30 PM IST — NSE publishes bhavcopy by
    # ~5-6 PM, so today's data is already up. We try today first; if today is
    # a weekend we fall back to the most recent weekday.
    from datetime import datetime as _dt
    _today = _dt.now().date()
    target = _today if not is_weekend(_today) else previous_trading_day(_today)
    log.info("Target trading date: %s", target)

    # 5. Idempotency
    if date_already_loaded(target):
        log.info("Date %s already loaded — nothing to do", target)
        return 0

    # 6. ETL
    res = process_date(target, symbols=symbols)
    status = res["status"]
    log.info("Daily ETL result: status=%s rows=%d", status, res["rows"])

    if status == "ok":
        log.info("==== Daily update OK ====")
        return 0
    if status == "holiday":
        log.info("==== Daily update: target was a holiday — no-op ====")
        return 0
    if status == "partial":
        log.error("==== Daily update FAILED: only one of bhavcopy/MTO available ====")
        return 1
    if status == "invalid":
        log.error("==== Daily update FAILED: validation errors ====")
        return 1
    log.error("==== Daily update FAILED: unknown status %s ====", status)
    return 1


if __name__ == "__main__":
    sys.exit(main())
