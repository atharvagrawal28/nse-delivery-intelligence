"""Download + validate NSE archive files with URL/format fallbacks.

All public functions return parsed pandas DataFrames on success and `None`
on failure (missing file, HTML error page, bad ZIP, schema mismatch).
Failures are logged; callers (ETL) decide whether to skip or halt.

Resilience design:
    * Each file type has a *list* of candidate URLs (config.*_URLS).
    * Each list is tried in order; the first response that passes content
      validation wins. The winning URL is logged.
    * For bhavcopy, we additionally fall back from the legacy CSV schema
      to the new BhavCopy_NSE_CM_*.csv.zip schema and normalize to a
      single canonical column set so the ETL doesn't care which one ran.
"""
from __future__ import annotations

import io
import logging
import shutil
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from config import (
    BHAVCOPY_LEGACY_URLS,
    BHAVCOPY_NEW_URLS,
    HTTP_HEADERS,
    MAX_RETRIES,
    MTO_URLS,
    NIFTY50_URLS,
    RAW_DIR,
    RAW_RETENTION_DAYS,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_BACKOFF_BASE_SECONDS,
)

log = logging.getLogger(__name__)


# Canonical bhavcopy columns the ETL consumes. Both legacy and new-format
# parsers normalize to this exact set.
CANONICAL_BHAV_COLS = [
    "SYMBOL", "SERIES",
    "OPEN", "HIGH", "LOW", "CLOSE",
    "LAST", "PREVCLOSE",
    "TOTTRDQTY", "TOTTRDVAL",
    "TIMESTAMP", "TOTALTRADES", "ISIN",
]

MTO_DETAIL_COLS = [
    "record_type", "sl_no", "symbol", "series",
    "traded_qty", "deliverable_qty", "delivery_pct",
]

# ---- MTO format detection signatures ----------------------------------------
# Old format (pre-2025): detail rows begin with "20050102,"
# New format (2025+):    detail rows begin with "20," after a descriptive header
# We detect which is present rather than assuming one or the other.
_MTO_OLD_PREFIX = "20050102,"
_MTO_NEW_PREFIX = "20,"
_MTO_NEW_HEADER_MARKER = "Name of Security"  # appears in the column-header line


# ---------------------------------------------------------------------------
# Raw storage
# ---------------------------------------------------------------------------
def raw_folder_for(d: date) -> Path:
    folder = RAW_DIR / d.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def clean_old_raw_folders() -> None:
    """Delete /data/raw/YYYY-MM-DD/ folders older than RAW_RETENTION_DAYS."""
    cutoff = date.today() - timedelta(days=RAW_RETENTION_DAYS)
    removed = 0
    for entry in RAW_DIR.iterdir():
        if not entry.is_dir():
            continue
        try:
            folder_date = datetime.strptime(entry.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if folder_date < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    log.info("Raw cleanup: removed %d folders older than %s", removed, cutoff)


# ---------------------------------------------------------------------------
# Low-level HTTP — single URL
# ---------------------------------------------------------------------------
def _looks_like_html(content: bytes, content_type: str) -> bool:
    if "html" in (content_type or "").lower():
        return True
    head = content[:200].lstrip().lower()
    return head.startswith(b"<")


def _content_type_ok(expect_kind: str, content: bytes, ctype: str) -> bool:
    ctype = (ctype or "").lower()
    if expect_kind == "zip":
        return ctype.startswith("application/") or content[:2] == b"PK"
    if expect_kind == "dat":
        return not ctype.startswith("text/html")
    if expect_kind == "csv":
        return not ctype.startswith("text/html")
    return True


def _fetch_one(url: str, expect_kind: str) -> Optional[bytes]:
    """Single-URL fetch with retries. Returns bytes, None on 404, or None on
    repeated failure. Distinguishes 404 (treated as 'not available') from
    other errors (retried)."""
    last_err: Optional[str] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info("GET %s (attempt %d/%d)", url, attempt, MAX_RETRIES)
            r = requests.get(
                url,
                headers=HTTP_HEADERS,
                timeout=REQUEST_TIMEOUT_SECONDS,
                allow_redirects=True,
            )
            if r.status_code == 404:
                log.info("404: %s", url)
                return None
            if r.status_code >= 400:
                last_err = f"HTTP {r.status_code}"
                raise requests.HTTPError(last_err)
            ctype = r.headers.get("Content-Type", "")
            if _looks_like_html(r.content, ctype):
                last_err = f"HTML/error page (content-type={ctype})"
                raise ValueError(last_err)
            if not _content_type_ok(expect_kind, r.content, ctype):
                last_err = f"unexpected content-type {ctype} for {expect_kind}"
                raise ValueError(last_err)
            return r.content
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            log.warning("Attempt %d failed for %s: %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                delay = RETRY_BACKOFF_BASE_SECONDS ** attempt
                log.info("Backing off %ds before retry", delay)
                time.sleep(delay)
    log.error("All %d attempts failed for %s — last error: %s",
              MAX_RETRIES, url, last_err)
    return None


def _try_urls(urls: list[str], expect_kind: str, **fmt) -> tuple[Optional[bytes], Optional[str]]:
    """Try each URL template in order. Returns (bytes, winning_url) or (None, None).

    404 from one URL falls through to the next; other errors also fall through
    after exhausting retries on the current URL.
    """
    for tpl in urls:
        url = tpl.format(**fmt)
        blob = _fetch_one(url, expect_kind)
        if blob is not None:
            log.info("Source resolved: %s", url)
            return blob, url
    return None, None


# ---------------------------------------------------------------------------
# Bhavcopy — legacy + new format with auto-fallback
# ---------------------------------------------------------------------------
LEGACY_REQUIRED = {
    "SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE",
    "LAST", "PREVCLOSE", "TOTTRDQTY", "TOTTRDVAL",
    "TIMESTAMP", "TOTALTRADES", "ISIN",
}

# New unified bhavcopy headers (NSE Cash Market consolidated file).
NEW_REQUIRED = {
    "TradDt", "Sgmt", "FinInstrmTp", "TckrSymb", "SctySrs",
    "OpnPric", "HghPric", "LwPric", "ClsPric",
    "LastPric", "PrvsClsgPric",
    "TtlTradgVol", "TtlTrfVal", "TtlNbOfTxsExctd", "ISIN",
}

NEW_TO_CANONICAL = {
    "TckrSymb":         "SYMBOL",
    "SctySrs":          "SERIES",
    "OpnPric":          "OPEN",
    "HghPric":          "HIGH",
    "LwPric":           "LOW",
    "ClsPric":          "CLOSE",
    "LastPric":         "LAST",
    "PrvsClsgPric":     "PREVCLOSE",
    "TtlTradgVol":      "TOTTRDQTY",
    "TtlTrfVal":        "TOTTRDVAL",
    "TtlNbOfTxsExctd":  "TOTALTRADES",
    "TradDt":           "TIMESTAMP",
    "ISIN":             "ISIN",
}


def _read_csv_from_zip(blob: bytes) -> Optional[pd.DataFrame]:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            if zf.testzip() is not None:
                log.error("ZIP integrity check failed")
                return None
            csv_name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
            if csv_name is None:
                log.error("No CSV inside bhavcopy ZIP")
                return None
            with zf.open(csv_name) as f:
                df = pd.read_csv(f)
    except zipfile.BadZipFile as e:
        log.error("Bad ZIP: %s", e)
        return None
    except Exception as e:  # noqa: BLE001
        log.error("Failed to parse bhavcopy ZIP: %s", e)
        return None
    df.columns = [c.strip() for c in df.columns]
    return df


def _normalize_legacy(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    missing = LEGACY_REQUIRED - set(df.columns)
    if missing:
        log.error("Legacy bhavcopy missing columns: %s", missing)
        return None
    return df[list(CANONICAL_BHAV_COLS)].copy()


def _normalize_new(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    missing = NEW_REQUIRED - set(df.columns)
    if missing:
        log.error("New bhavcopy missing columns: %s", missing)
        return None
    # Keep cash-market equity rows only (drop derivatives, indices).
    df = df[df["Sgmt"].astype(str).str.upper().eq("CM")]
    df = df[df["FinInstrmTp"].astype(str).str.upper().isin({"STK", "EQ"})]
    if df.empty:
        log.error("New bhavcopy parsed but had 0 equity rows after Sgmt/FinInstrmTp filter")
        return None
    out = df.rename(columns=NEW_TO_CANONICAL)
    for c in CANONICAL_BHAV_COLS:
        if c not in out.columns:
            out[c] = pd.NA  # not all new-format files include LAST/PREVCLOSE for every row
    return out[CANONICAL_BHAV_COLS].copy()


def download_bhavcopy(d: date) -> Optional[pd.DataFrame]:
    """Download + parse the daily bhavcopy for date `d`.

    Strategy:
        1. Try the legacy URL list (cm{DD}{MMM}{YYYY}bhav.csv.zip).
        2. If none of those return data, try the new URL list
           (BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip) and normalize
           its column names to the canonical set.

    Returns a DataFrame with columns == CANONICAL_BHAV_COLS, or None on
    missing/invalid data.
    """
    yyyy = d.strftime("%Y")
    mmm = d.strftime("%b").upper()
    dd = d.strftime("%d")
    yyyymmdd = d.strftime("%Y%m%d")

    # ---- 1. Legacy ----
    blob, winner = _try_urls(
        BHAVCOPY_LEGACY_URLS, expect_kind="zip",
        yyyy=yyyy, mmm=mmm, dd=dd,
    )
    if blob is not None:
        raw_path = raw_folder_for(d) / f"cm{dd}{mmm}{yyyy}bhav.csv.zip"
        raw_path.write_bytes(blob)
        df = _read_csv_from_zip(blob)
        if df is not None:
            norm = _normalize_legacy(df)
            if norm is not None and len(norm) > 0:
                log.info("Bhavcopy %s: %d rows (legacy format, %s)", d, len(norm), winner)
                return norm
        log.warning("Bhavcopy %s: legacy URL responded but content unusable — trying new format", d)

    # ---- 2. New unified format ----
    blob, winner = _try_urls(
        BHAVCOPY_NEW_URLS, expect_kind="zip",
        yyyymmdd=yyyymmdd,
    )
    if blob is not None:
        raw_path = raw_folder_for(d) / f"BhavCopy_NSE_CM_{yyyymmdd}.csv.zip"
        raw_path.write_bytes(blob)
        df = _read_csv_from_zip(blob)
        if df is not None:
            norm = _normalize_new(df)
            if norm is not None and len(norm) > 0:
                log.info("Bhavcopy %s: %d rows (new format, %s)", d, len(norm), winner)
                return norm
        log.error("Bhavcopy %s: new URL responded but content unusable", d)

    log.info("Bhavcopy %s: no source returned usable data (likely non-trading day)", d)
    return None


# ---------------------------------------------------------------------------
# MTO
# ---------------------------------------------------------------------------
def _parse_mto_blob(blob: bytes, label: str) -> Optional[pd.DataFrame]:
    """Format-agnostic MTO parser.

    NSE has used at least two layouts:
      Old (pre-2025): detail rows start with "20050102,"
      New (2025+):    detail rows start with "20," after a descriptive header

    Strategy: detect which prefix is present by scanning the file.
    Falls back to a field-count heuristic if neither prefix is found,
    so future record-type renaming doesn't break the pipeline.
    """
    text = blob.decode("utf-8", errors="replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # --- Detect format ---
    has_old = any(ln.startswith(_MTO_OLD_PREFIX) for ln in lines)
    has_new_header = any(_MTO_NEW_HEADER_MARKER in ln for ln in lines)

    if has_old:
        detail_lines = [ln for ln in lines if ln.startswith(_MTO_OLD_PREFIX)]
        log.info("MTO %s: old format detected (%d detail rows)", label, len(detail_lines))
    elif has_new_header:
        detail_lines = [ln for ln in lines if ln.startswith(_MTO_NEW_PREFIX)]
        log.info("MTO %s: new format detected (%d detail rows)", label, len(detail_lines))
    else:
        # Heuristic: pick any line with exactly 7 comma-separated fields
        # where fields[4] and [5] are numeric (the quantity columns).
        detail_lines = []
        for ln in lines:
            parts = ln.split(",")
            if len(parts) == 7:
                try:
                    int(parts[4].strip())
                    int(parts[5].strip())
                    detail_lines.append(ln)
                except ValueError:
                    pass
        if detail_lines:
            log.warning(
                "MTO %s: unrecognised format — used field-count heuristic (%d rows). "
                "Inspect raw file and update parser if counts look wrong.",
                label, len(detail_lines),
            )
        else:
            log.error("MTO %s: no detail records found in any known format", label)
            return None

    if not detail_lines:
        log.error("MTO %s: detail prefix found but 0 matching rows", label)
        return None

    try:
        buf = io.StringIO("\n".join(detail_lines))
        df = pd.read_csv(buf, header=None, names=MTO_DETAIL_COLS)
    except Exception as e:  # noqa: BLE001
        log.error("Failed to parse MTO detail rows for %s: %s", label, e)
        return None

    if len(df) == 0:
        log.error("MTO %s: 0 rows after CSV parse", label)
        return None

    for col in ("symbol", "series"):
        df[col] = df[col].astype(str).str.strip()
    for col in ("traded_qty", "deliverable_qty"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["delivery_pct"] = pd.to_numeric(df["delivery_pct"], errors="coerce")
    return df


def download_mto(d: date) -> Optional[pd.DataFrame]:
    """Download + parse the MTO delivery DAT for date `d`.

    Handles both the legacy format (detail rows: '20050102,...') and the
    current format (detail rows: '20,...' after a descriptive header).
    See `_parse_mto_blob` for the detection logic.
    """
    ddmmyyyy = d.strftime("%d%m%Y")
    blob, winner = _try_urls(MTO_URLS, expect_kind="dat", ddmmyyyy=ddmmyyyy)
    if blob is None:
        return None

    raw_path = raw_folder_for(d) / f"MTO_{ddmmyyyy}.DAT"
    raw_path.write_bytes(blob)

    df = _parse_mto_blob(blob, label=str(d))
    if df is None:
        return None

    log.info("MTO %s: %d rows (%s)", d, len(df), winner)
    return df


# ---------------------------------------------------------------------------
# NIFTY 50 list
# ---------------------------------------------------------------------------
def download_nifty50_list() -> Optional[pd.DataFrame]:
    """Return DataFrame with at least 'Symbol' column, or None."""
    blob, winner = _try_urls(NIFTY50_URLS, expect_kind="csv")
    if blob is None:
        return None

    raw_path = raw_folder_for(date.today()) / "ind_nifty50list.csv"
    raw_path.write_bytes(blob)

    try:
        df = pd.read_csv(io.BytesIO(blob))
    except Exception as e:  # noqa: BLE001
        log.error("Failed to parse nifty50 list (%s): %s", winner, e)
        return None

    df.columns = [c.strip() for c in df.columns]
    if "Symbol" not in df.columns:
        log.error("Nifty50 list missing 'Symbol' column: %s", df.columns.tolist())
        return None

    df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
    df = df[df["Symbol"].str.len() > 0]
    if len(df) == 0:
        log.error("Nifty50 list parsed to 0 rows")
        return None

    log.info("Nifty50 list: %d symbols (%s)", len(df), winner)
    return df
