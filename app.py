"""NSE Delivery Intelligence Terminal — main Streamlit app.

Shared UI helpers (grid options, exports, formatters, footer) live in
ui_helpers.py so that pages can import them directly without going through
this file. This avoids Streamlit's hot-reload module-cache bug where pages
importing from app.py would get a stale cached version after a hot-reload.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import streamlit as st

from analytics import decorate, load_history
from config import configure_logging
from ui_helpers import (
    build_grid_options,
    cached_symbols,
    inject_global_css,
    prepare_display_df,
    render_aggrid,
    render_footer,
    to_csv_bytes,
    to_excel_bytes,
)

configure_logging()
log = logging.getLogger(__name__)


# ============================================================================
# Cached data loaders — unique to main page (pages use their own loaders)
# ============================================================================
@st.cache_data(ttl=300, show_spinner=False)
def cached_history(symbols: tuple[str, ...] | None, start: date, end: date):
    syms = list(symbols) if symbols else None
    return load_history(symbols=syms, start_date=start, end_date=end)


def _date_bounds_from_db() -> tuple[date, date]:
    """Return (earliest, latest) date present in delivery_history."""
    from db import cursor as _cursor
    with _cursor() as cur:
        cur.execute("SELECT MIN(date), MAX(date) FROM delivery_history")
        lo, hi = cur.fetchone()
    if lo is None or hi is None:
        today = date.today()
        return today - timedelta(days=365), today - timedelta(days=1)
    return lo, hi


# ============================================================================
# Main page render
# ============================================================================
def render() -> None:
    st.set_page_config(
        page_title="NSE Delivery Intelligence",
        page_icon="📊",
        layout="wide",
    )
    inject_global_css()
    st.title("NSE Delivery Intelligence Terminal")

    all_symbols = cached_symbols()
    if not all_symbols:
        st.warning(
            "No symbols found in `stock_universe`. "
            "Run `python historical_backfill.py` first."
        )
        return

    db_min, db_max = _date_bounds_from_db()

    _PRESETS = {
        "1 Day":    1,
        "1 Week":   7,
        "1 Month":  30,
        "3 Months": 90,
        "6 Months": 180,
        "1 Year":   365,
        "Custom":   None,
    }

    with st.sidebar:
        st.header("Filters")

        preset = st.selectbox(
            "Quick range",
            options=list(_PRESETS.keys()),
            index=2,
        )

        if preset != "Custom":
            start_date = max(db_min, db_max - timedelta(days=_PRESETS[preset]))
            end_date = db_max
            st.caption(f"{start_date.strftime('%d %b %Y')}  →  {end_date.strftime('%d %b %Y')}")
        else:
            default_start = max(db_min, db_max - timedelta(days=30))
            date_range = st.date_input(
                "Custom date range",
                value=(default_start, db_max),
                min_value=db_min,
                max_value=db_max,
            )
            if isinstance(date_range, tuple) and len(date_range) == 2:
                start_date, end_date = date_range
            else:
                start_date, end_date = default_start, db_max

        st.divider()
        picked = st.multiselect(
            "Symbols (empty = all NIFTY50)",
            options=all_symbols,
            default=[],
        )
        show_analytics = st.checkbox("Show analytics columns", value=False)
        st.divider()
        st.caption(
            "Analytics columns: 5D / 20D rolling avg, 1-year rolling "
            "percentile (last 250 sessions), signal classification."
        )

    sym_filter = tuple(picked) if picked else None
    df_raw = cached_history(sym_filter, start_date, end_date)

    if df_raw.empty:
        st.info("No rows for the selected filters.")
        return

    df = decorate(df_raw)
    df_disp = prepare_display_df(df)
    df_disp = df_disp.sort_values(["date", "symbol"], ascending=[False, True])

    col_a, col_b, col_c = st.columns([2, 1, 1])
    col_a.metric("Rows", f"{len(df_disp):,}")
    col_b.metric("Symbols", f"{df_disp['symbol'].nunique()}")
    col_c.metric("Sessions", f"{df_disp['date'].nunique()}")

    render_aggrid(df_disp, show_analytics, height=620)

    st.divider()
    c1, c2 = st.columns(2)
    c1.download_button(
        "Download CSV",
        data=to_csv_bytes(df_disp, show_analytics),
        file_name=f"nse_delivery_{start_date}_{end_date}.csv",
        mime="text/csv",
    )
    c2.download_button(
        "Download Excel",
        data=to_excel_bytes(df_disp, show_analytics),
        file_name=f"nse_delivery_{start_date}_{end_date}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    render_footer()


if __name__ == "__main__":
    render()
