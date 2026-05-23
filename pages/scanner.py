"""Scanner page — filter latest-row signals across the active universe."""
from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from analytics import decorate, latest_per_symbol, load_history
from ui_helpers import (
    cached_symbols,
    inject_global_css,
    prepare_display_df,
    render_aggrid,
    render_footer,
    to_csv_bytes,
    to_excel_bytes,
)

st.set_page_config(page_title="Scanner — NSE Delivery", layout="wide")
inject_global_css()
st.title("Scanner")

symbols = cached_symbols()
if not symbols:
    st.warning("Stock universe is empty. Run `python historical_backfill.py`.")
    st.stop()

with st.sidebar:
    st.header("Scanner filters")
    lookback_days = st.slider("Lookback window (days)", 30, 365, 60, step=15)
    signals_to_show = st.multiselect(
        "Show signals",
        options=["Accumulation", "Distribution", "Elevated", "Thin/Speculative", "Neutral"],
        default=["Accumulation", "Distribution", "Elevated"],
    )
    min_percentile = st.slider("Minimum 1Y percentile", 0, 100, 60, step=5)

end = date.today()
start = end - timedelta(days=lookback_days)
df = load_history(symbols=symbols, start_date=start, end_date=end)
if df.empty:
    st.info("No data in selected window.")
    st.stop()

df = decorate(df)
latest = latest_per_symbol(df)
latest = latest[latest["signal"].isin(signals_to_show)]
latest = latest[latest["delivery_pct_percentile_1y"].fillna(0) >= min_percentile]
latest = latest.sort_values(
    ["signal", "delivery_pct_percentile_1y"], ascending=[True, False]
)

if latest.empty:
    st.info("No symbols match the selected filters.")
    st.stop()

st.metric("Matches", f"{len(latest)}")
disp = prepare_display_df(latest)

render_aggrid(disp, show_analytics=True, height=520)

st.divider()
c1, c2 = st.columns(2)
c1.download_button(
    "Download CSV",
    data=to_csv_bytes(disp, show_analytics=True),
    file_name=f"scanner_{end}.csv",
    mime="text/csv",
)
c2.download_button(
    "Download Excel",
    data=to_excel_bytes(disp, show_analytics=True),
    file_name=f"scanner_{end}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

render_footer()
