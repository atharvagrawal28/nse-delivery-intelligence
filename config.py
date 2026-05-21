"""Centralized configuration for the NSE Delivery Intelligence Terminal."""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Paths --------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

# Keep raw downloads for this many days, then purge at the start of daily runs.
RAW_RETENTION_DAYS = 7

# --- Database -----------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "")

# --- NSE archive URLs ---------------------------------------------------------
# NSE has migrated several times. Each list below is tried in order; the first
# response that passes content validation wins. Easy to extend if NSE moves
# things again — no code changes required.

# Legacy bhavcopy ZIP: cm{DD}{MMM}{YYYY}bhav.csv.zip (covers history pre-Jul 2024).
BHAVCOPY_LEGACY_URLS = [
    "https://archives.nseindia.com/content/historical/EQUITIES/"
    "{yyyy}/{mmm}/cm{dd}{mmm}{yyyy}bhav.csv.zip",
    "https://nsearchives.nseindia.com/content/historical/EQUITIES/"
    "{yyyy}/{mmm}/cm{dd}{mmm}{yyyy}bhav.csv.zip",
]

# New unified bhavcopy ZIP: BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip
# (covers Jul 2024 onward; different column schema — see nse_downloader).
BHAVCOPY_NEW_URLS = [
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip",
    "https://archives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip",
]

# MTO delivery DAT
MTO_URLS = [
    "https://archives.nseindia.com/archives/equities/mto/MTO_{ddmmyyyy}.DAT",
    "https://nsearchives.nseindia.com/archives/equities/mto/MTO_{ddmmyyyy}.DAT",
]

# Nifty 50 constituents
NIFTY50_URLS = [
    "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
    "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
]

# A real-browser UA is required by NSE's edge layer even for archive files.
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Referer": "https://www.nseindia.com/",
}

# --- Retry / timeout ----------------------------------------------------------
MAX_RETRIES = 3
RETRY_BACKOFF_BASE_SECONDS = 2  # 2, 4, 8 ...
REQUEST_TIMEOUT_SECONDS = 30

# --- Backfill -----------------------------------------------------------------
BACKFILL_DAYS = 365
BACKFILL_CHUNK_DAYS = 30  # progress logging granularity

# --- Analytics ----------------------------------------------------------------
ROLLING_5D = 5
ROLLING_20D = 20
PERCENTILE_WINDOW_SESSIONS = 250  # ~1 trading year

# Signal classification thresholds (deterministic, explainable).
SIGNAL_THRESHOLDS = {
    "accumulation": {"avg_mult": 1.15, "percentile_min": 75, "dod_min": 0.0},
    "distribution": {"avg_mult": 1.10, "percentile_min": 65, "dod_max": -15.0},
    "elevated":     {"avg_mult": 1.00, "percentile_min": 60},
    "thin":         {"avg_mult": 0.80, "percentile_max": 30},
}

# --- DoD cell-style thresholds ------------------------------------------------
# Single source of truth. AgGrid JsCode is generated from this dict at runtime
# (see app.py `_build_dod_cell_style`). Never hardcode these colors in JS strings.
DOD_THRESHOLDS = {
    "positive": [
        {"above": 150, "bg": "#155724", "fg": "#ffffff", "fw": "700"},
        {"above": 100, "bg": "#28a745", "fg": "#ffffff", "fw": "400"},
        {"above":  50, "bg": "#c3e6cb", "fg": "#155724", "fw": "400"},
    ],
    "negative": [
        {"below": -100, "bg": "#721c24", "fg": "#ffffff", "fw": "700"},
        {"below":  -50, "bg": "#f5c6cb", "fg": "#721c24", "fw": "400"},
    ],
}


# --- Logging ------------------------------------------------------------------
def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent logging setup."""
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # quiet noisy libs
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
