# NSE Delivery Intelligence Terminal

A production-grade, minimal, single-maintainer system for tracking NIFTY 50
delivery data from official NSE archives. Strictly linear architecture:

```
nse_downloader → etl → PostgreSQL → Streamlit UI → exports
```

No Playwright, no async, no class hierarchies, no Docker. Free-tier friendly
(Streamlit Community Cloud + Supabase/Neon Postgres + GitHub Actions).

## Layout

| File | Purpose |
|---|---|
| `config.py` | Paths, URLs, DoD threshold dict, retry/backoff, signal thresholds |
| `db.py` | psycopg2 helpers + schema init (`python db.py` to create tables) |
| `nse_downloader.py` | Download + validate bhavcopy ZIP, MTO DAT, NIFTY50 list |
| `universe.py` | Sync NIFTY50 list against `stock_universe`, backfill new symbols |
| `etl.py` | Merge bhavcopy + MTO, `validate()` pipeline, upsert into `delivery_history` |
| `analytics.py` | DoD %, 5D/20D rolling avg, 1Y percentile (250 sessions), signal class |
| `historical_backfill.py` | Backfill last 365 days for all active symbols (chunked, idempotent) |
| `update_daily.py` | Daily runner: housekeeping → universe sync → ETL for prev trading day |
| `app.py` | Streamlit main page + shared AgGrid helpers |
| `pages/scanner.py` | Filter latest-row signals across the universe |
| `pages/stock_detail.py` | Per-symbol history table + Plotly chart |
| `.github/workflows/daily_update.yml` | 18:30 IST weekday cron |

## Setup

1. **Create a Postgres database** (Supabase / Neon / Railway / local).
2. **Set `DATABASE_URL`** in `.env` (local) or as a GitHub / Streamlit secret.
   ```
   DATABASE_URL=postgresql://user:pass@host:5432/db
   ```
3. **Install deps**: `pip install -r requirements.txt`
4. **Initialize schema**: `python db.py`
5. **Backfill 365 days**: `python historical_backfill.py`
6. **Run the UI**: `streamlit run app.py`

## Daily automation

`update_daily.py` is the cron entry point. It:

1. Deletes `/data/raw/YYYY-MM-DD/` folders older than 7 days.
2. Syncs the NIFTY 50 universe (adds/removes symbols, preserves history).
3. Backfills 365 days for any newly added symbol.
4. Computes the previous trading day, skips if already loaded, otherwise
   downloads → validates → upserts.
5. Exits 1 on hard failure so the GitHub Action surfaces a red check.

## Data sources

All downloads hit official NSE archive paths with no auth or cookies:

- Bhavcopy: `https://archives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MMM}/cm{DD}{MMM}{YYYY}bhav.csv.zip`
- MTO DAT: `https://archives.nseindia.com/archives/equities/mto/MTO_{DDMMYYYY}.DAT`
- NIFTY 50: `https://archives.nseindia.com/content/indices/ind_nifty50list.csv`

The downloader rejects HTML/error responses (NSE returns 200 + HTML when a
file is missing), validates ZIP integrity, checks required columns, and
retries 3× with exponential backoff. A 404 is treated as a non-trading day
(INFO log, not error).

## Schema

```sql
stock_universe (symbol PK, active, added_date, removed_date)

delivery_history (
  symbol, series, date,
  traded_qty, turnover, trades,
  deliverable_qty, delivery_pct, close_price,
  UNIQUE(symbol, series, date)
)
```

`close_price` is captured from bhavcopy's CLOSE column for future signal work.

## Signal classification (deterministic, explainable)

| Signal | Condition |
|---|---|
| Accumulation     | `delivery_pct > 20D_avg × 1.15` AND `percentile ≥ 75` AND `dod ≥ 0` |
| Distribution     | `delivery_pct > 20D_avg × 1.10` AND `percentile ≥ 65` AND `dod < -15` |
| Elevated         | `delivery_pct > 20D_avg` AND `percentile ≥ 60` |
| Thin/Speculative | `delivery_pct < 20D_avg × 0.80` AND `percentile ≤ 30` |
| Neutral          | everything else |

Thresholds live in `config.SIGNAL_THRESHOLDS` and DoD cell colors in
`config.DOD_THRESHOLDS`. AgGrid JsCode for the DoD cell style is generated
from that dict at runtime (no hex strings in JS).

## Exports

CSV and Excel are produced client-side via `st.download_button`. Excel
preserves Indian number formatting (`#,##,##0` / `#,##,##0.00`) and auto-widths
columns.
