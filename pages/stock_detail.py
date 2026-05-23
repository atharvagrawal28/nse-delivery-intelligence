"""Stock detail page — full history table + delivery % chart for one symbol."""
from __future__ import annotations

from datetime import date, timedelta

import plotly.graph_objects as go
import streamlit as st

from analytics import decorate, load_history
from ui_helpers import (
    cached_symbols,
    inject_global_css,
    prepare_display_df,
    render_aggrid,
    render_footer,
    to_csv_bytes,
    to_excel_bytes,
)

st.set_page_config(page_title="Stock Detail — NSE Delivery", layout="wide", initial_sidebar_state="expanded")
inject_global_css()
st.title("Stock Detail")

symbols = cached_symbols()
if not symbols:
    st.warning("Stock universe is empty. Run `python historical_backfill.py`.")
    st.stop()

with st.sidebar:
    st.header("Symbol")
    symbol = st.selectbox("Pick a symbol", options=symbols, index=0)
    lookback_days = st.slider("Lookback window (days)", 30, 365, 180, step=30)

end = date.today()
start = end - timedelta(days=lookback_days)
df = load_history(symbols=[symbol], start_date=start, end_date=end)
if df.empty:
    st.info(f"No data for {symbol} in the selected window.")
    st.stop()

df = decorate(df)
disp = prepare_display_df(df).sort_values("date", ascending=False)

last = df.sort_values("date").iloc[-1]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Symbol", symbol)
c2.metric("Last Close ₹", f"{float(last['close_price']):,.2f}")
c3.metric("Last Delivery %", f"{float(last['delivery_pct']):.2f}")
c4.metric("Signal", str(last["signal"]) if "signal" in df.columns else "—")

fig = go.Figure()
fig.add_trace(go.Scatter(
    x=df["date"], y=df["delivery_pct"],
    mode="lines", name="Delivery %",
    line=dict(color="#4e8cff", width=2),
))
fig.add_trace(go.Scatter(
    x=df["date"], y=df["delivery_pct_20d_avg"],
    mode="lines", name="20D Avg %",
    line=dict(color="#ff7f0e", dash="dash", width=1.5),
))
fig.add_trace(go.Scatter(
    x=df["date"], y=df["close_price"],
    mode="lines", name="Close ₹",
    line=dict(color="#2ca02c", width=1.5), yaxis="y2",
))
fig.update_layout(
    height=360,
    margin=dict(l=10, r=10, t=30, b=10),
    paper_bgcolor="#0e1117",
    plot_bgcolor="#0e1117",
    font=dict(color="#c0c8d8"),
    yaxis=dict(title="Delivery %", gridcolor="#1e2a3a", color="#7a8a9a"),
    yaxis2=dict(title="Close ₹", overlaying="y", side="right",
                gridcolor="#1e2a3a", color="#7a8a9a"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02,
                xanchor="right", x=1, font=dict(size=11)),
    hovermode="x unified",
)
st.plotly_chart(fig, use_container_width=True)

st.subheader("Daily history")
render_aggrid(disp, show_analytics=True, height=520)

st.divider()
c1, c2 = st.columns(2)
c1.download_button(
    "Download CSV",
    data=to_csv_bytes(disp, show_analytics=True),
    file_name=f"{symbol}_{start}_{end}.csv",
    mime="text/csv",
)
c2.download_button(
    "Download Excel",
    data=to_excel_bytes(disp, show_analytics=True),
    file_name=f"{symbol}_{start}_{end}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

render_footer()
