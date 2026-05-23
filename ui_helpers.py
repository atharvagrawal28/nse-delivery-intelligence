"""Shared UI helpers — imported by app.py AND pages/*.py.

Keeping this in a separate module avoids the Streamlit hot-reload cache bug
where pages importing from app.py get a stale cached module after hot-reload
instead of the freshly updated one.
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
import streamlit as st

from config import DOD_THRESHOLDS
from universe import get_active_symbols


# ============================================================================
# Professional CSS — injected once per page via inject_global_css()
# ============================================================================
_GLOBAL_CSS = """
<style>
/* ── Hide Streamlit cloud toolbar (Share / GitHub / star / pencil / ⋮) ─── */
[data-testid="stToolbar"]          { display: none !important; }
[data-testid="stDecoration"]       { display: none !important; }
[data-testid="stStatusWidget"]     { display: none !important; }
#MainMenu                          { display: none !important; }
footer                             { display: none !important; }

/* ── Clean header ──────────────────────────────────────────────────────── */
[data-testid="stHeader"] {
    background: transparent !important;
    border-bottom: none !important;
}

/* ── Tighter sidebar ───────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: #0e1117;
    border-right: 1px solid #1e2530;
}
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stSlider label,
[data-testid="stSidebar"] .stMultiSelect label,
[data-testid="stSidebar"] .stCheckbox label {
    font-size: 0.82rem;
    color: #c0c8d8;
    letter-spacing: 0.02em;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #e0e8f8;
    font-size: 0.95rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
}

/* ── Metric cards ──────────────────────────────────────────────────────── */
[data-testid="stMetric"] {
    background: #161b27;
    border: 1px solid #1e2a3a;
    border-radius: 8px;
    padding: 12px 16px;
}
[data-testid="stMetricLabel"] { color: #7a8a9a; font-size: 0.78rem; }
[data-testid="stMetricValue"] { color: #e8f0fe; font-size: 1.5rem; font-weight: 700; }

/* ── Download buttons ──────────────────────────────────────────────────── */
[data-testid="stDownloadButton"] > button {
    width: 100%;
    border-radius: 6px;
    font-size: 0.82rem;
    font-weight: 600;
    padding: 8px 16px;
    border: 1px solid #2a3a50;
    transition: background 0.15s;
}

/* ── AgGrid container ──────────────────────────────────────────────────── */
.ag-root-wrapper {
    border-radius: 6px !important;
    border: 1px solid #1e2a3a !important;
    overflow: hidden;
}
.ag-header { border-bottom: 2px solid #2a3a50 !important; }
.ag-row-even { background-color: #0e1117 !important; }
.ag-row-odd  { background-color: #111820 !important; }
.ag-row:hover { background-color: #1a2332 !important; }

/* ── Subheader / section labels ────────────────────────────────────────── */
h2, h3 {
    color: #c8d8f0;
    font-weight: 600;
}
</style>
"""


def inject_global_css() -> None:
    """Call once at the top of every page to apply shared styling."""
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)


# ============================================================================
# AgGrid — lazy import so non-Streamlit scripts (backfill, ETL) don't need it
# ============================================================================
def _aggrid_imports():
    from st_aggrid import AgGrid, GridOptionsBuilder, JsCode  # noqa: F401
    return AgGrid, GridOptionsBuilder, JsCode


# ============================================================================
# AgGrid JS — exactly 3 reusable blocks
# ============================================================================
def build_dod_cell_style():
    """Block 1: cellStyle_dod — generated from DOD_THRESHOLDS (no hardcoded hex)."""
    _, _, JsCode = _aggrid_imports()
    pos = sorted(DOD_THRESHOLDS["positive"], key=lambda x: -x["above"])
    neg = sorted(DOD_THRESHOLDS["negative"], key=lambda x: x["below"])
    branches: list[str] = []
    for r in pos:
        branches.append(
            f'  if (v >= {r["above"]}) '
            f'return {{backgroundColor: "{r["bg"]}", color: "{r["fg"]}", '
            f'fontWeight: "{r["fw"]}"}};'
        )
    for r in neg:
        branches.append(
            f'  if (v <= {r["below"]}) '
            f'return {{backgroundColor: "{r["bg"]}", color: "{r["fg"]}", '
            f'fontWeight: "{r["fw"]}"}};'
        )
    body = "\n".join(branches)
    return JsCode(
        "function(params) {\n"
        "  if (params.value == null || isNaN(params.value)) return null;\n"
        "  const v = params.value;\n"
        f"{body}\n"
        "  return null;\n"
        "}"
    )


def _indian_int_formatter():
    _, _, JsCode = _aggrid_imports()
    return JsCode("""
function(params) {
  const v = params.value;
  if (v == null || v === '') return '';
  const n = Number(v);
  if (!isFinite(n)) return '';
  const neg = n < 0;
  const abs = Math.abs(Math.trunc(n));
  const s = abs.toString();
  let out;
  if (s.length <= 3) { out = s; }
  else {
    const last3 = s.slice(-3);
    const rest = s.slice(0, -3);
    out = rest.replace(/\\B(?=(\\d{2})+(?!\\d))/g, ',') + ',' + last3;
  }
  return neg ? '-' + out : out;
}
""")


def _indian_decimal_formatter():
    _, _, JsCode = _aggrid_imports()
    return JsCode("""
function(params) {
  const v = params.value;
  if (v == null || v === '') return '';
  const n = Number(v);
  if (!isFinite(n)) return '';
  const neg = n < 0;
  const abs = Math.abs(n);
  const fixed = abs.toFixed(2);
  const [intPart, decPart] = fixed.split('.');
  let out;
  if (intPart.length <= 3) { out = intPart; }
  else {
    const last3 = intPart.slice(-3);
    const rest = intPart.slice(0, -3);
    out = rest.replace(/\\B(?=(\\d{2})+(?!\\d))/g, ',') + ',' + last3;
  }
  return (neg ? '-' : '') + out + '.' + decPart;
}
""")


# ============================================================================
# DataFrame prep
# ============================================================================
def prepare_display_df(df: pd.DataFrame) -> pd.DataFrame:
    """Add display columns and pre-round Python-formatted values."""
    if df.empty:
        return df
    out = df.copy()
    out["date_display"] = pd.to_datetime(out["date"]).dt.strftime("%d-%b-%y")
    out["delivery_pct"] = out["delivery_pct"].round(2)
    for c in ("dod_traded_qty", "dod_turnover", "dod_deliverable_qty"):
        if c in out.columns:
            out[c] = out[c].round(1)
    for c in ("delivery_pct_5d_avg", "delivery_pct_20d_avg", "delivery_pct_percentile_1y"):
        if c in out.columns:
            out[c] = out[c].round(2)
    return out


# ============================================================================
# AgGrid options
# ============================================================================
def build_grid_options(df: pd.DataFrame, show_analytics: bool) -> dict:
    """Build AgGrid options with exact column order from spec."""
    _, GridOptionsBuilder, JsCode = _aggrid_imports()
    cell_style_dod   = build_dod_cell_style()
    int_fmt          = _indian_int_formatter()
    dec_fmt          = _indian_decimal_formatter()

    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(sortable=True, filter=True, resizable=True)

    gb.configure_column("symbol",          header_name="Symbol", pinned="left", width=110)
    gb.configure_column("series",          header_name="Series", width=80)
    gb.configure_column("date_display",    header_name="Date", width=100)
    gb.configure_column("traded_qty",      header_name="Total Traded Qty",
                        valueFormatter=int_fmt, type=["numericColumn"], width=155)
    gb.configure_column("dod_traded_qty",  header_name="DoD Chg",
                        cellStyle=cell_style_dod, type=["numericColumn"], width=95)
    gb.configure_column("turnover",        header_name="Turnover ₹",
                        valueFormatter=dec_fmt, type=["numericColumn"], width=165)
    gb.configure_column("dod_turnover",    header_name="DoD Chg",
                        cellStyle=cell_style_dod, type=["numericColumn"], width=95)
    gb.configure_column("trades",          header_name="No. of Trades",
                        valueFormatter=int_fmt, type=["numericColumn"], width=125)
    gb.configure_column("deliverable_qty", header_name="Deliverable Qty",
                        valueFormatter=int_fmt, type=["numericColumn"], width=145)
    gb.configure_column("dod_deliverable_qty", header_name="DoD Chg",
                        cellStyle=cell_style_dod, type=["numericColumn"], width=95)
    gb.configure_column("delivery_pct",    header_name="% Dly to Traded",
                        type=["numericColumn"], width=140)

    if show_analytics:
        gb.configure_column("delivery_pct_5d_avg",        header_name="5D Avg %",
                            type=["numericColumn"], width=100)
        gb.configure_column("delivery_pct_20d_avg",       header_name="20D Avg %",
                            type=["numericColumn"], width=100)
        gb.configure_column("delivery_pct_percentile_1y", header_name="1Y Pctile",
                            type=["numericColumn"], width=100)
        gb.configure_column("signal",                     header_name="Signal", width=135)
    else:
        for c in ("delivery_pct_5d_avg", "delivery_pct_20d_avg",
                  "delivery_pct_percentile_1y", "signal"):
            if c in df.columns:
                gb.configure_column(c, hide=True)

    for c in ("close_price", "date"):
        if c in df.columns:
            gb.configure_column(c, hide=True)

    options = gb.build()
    options["rowHeight"] = 26
    options["headerHeight"] = 38
    options["onFirstDataRendered"] = JsCode(
        "function(p){"
        "  var ca = p.columnApi || p.api;"
        "  if(ca && ca.autoSizeAllColumns) ca.autoSizeAllColumns();"
        "}"
    )

    desired = [
        "symbol", "series", "date_display",
        "traded_qty", "dod_traded_qty",
        "turnover", "dod_turnover",
        "trades",
        "deliverable_qty", "dod_deliverable_qty",
        "delivery_pct",
    ]
    if show_analytics:
        desired += ["delivery_pct_5d_avg", "delivery_pct_20d_avg",
                    "delivery_pct_percentile_1y", "signal"]
    by_field = {c["field"]: c for c in options["columnDefs"]}
    ordered = [by_field[f] for f in desired if f in by_field]
    ordered += [c for c in options["columnDefs"] if c["field"] not in desired]
    options["columnDefs"] = ordered
    return options


# ============================================================================
# AgGrid render with fallback
# ============================================================================
def render_aggrid(df: pd.DataFrame, show_analytics: bool, height: int = 520) -> None:
    """Render AgGrid with automatic fallback to st.dataframe on unsupported browsers."""
    try:
        AgGrid, _, _ = _aggrid_imports()
        AgGrid(
            df,
            gridOptions=build_grid_options(df, show_analytics),
            theme="alpine",
            allow_unsafe_jscode=True,
            fit_columns_on_grid_load=False,
            height=height,
            update_mode="NO_UPDATE",
            enable_enterprise_modules=False,
        )
    except Exception:
        # Fallback: plain Streamlit dataframe (works on all browsers / environments)
        _cols_to_show = [
            c for c in [
                "date_display", "symbol", "series",
                "traded_qty", "dod_traded_qty",
                "turnover", "dod_turnover",
                "trades", "deliverable_qty", "dod_deliverable_qty",
                "delivery_pct",
                *(["delivery_pct_5d_avg", "delivery_pct_20d_avg",
                   "delivery_pct_percentile_1y", "signal"] if show_analytics else []),
            ] if c in df.columns
        ]
        st.dataframe(df[_cols_to_show], use_container_width=True, height=height)


# ============================================================================
# Exports
# ============================================================================
_EXPORT_COLUMN_MAP = {
    "symbol": "Symbol",
    "series": "Series",
    "date_display": "Date",
    "traded_qty": "Total Traded Quantity",
    "dod_traded_qty": "DoD % (Traded Qty)",
    "turnover": "Turnover ₹",
    "dod_turnover": "DoD % (Turnover)",
    "trades": "No. of Trades",
    "deliverable_qty": "Deliverable Qty",
    "dod_deliverable_qty": "DoD % (Deliverable Qty)",
    "delivery_pct": "% Dly Qt to Traded Qty",
    "delivery_pct_5d_avg": "5D Avg %",
    "delivery_pct_20d_avg": "20D Avg %",
    "delivery_pct_percentile_1y": "1Y Percentile",
    "signal": "Signal",
}

_COL_FORMAT = {
    "Total Traded Quantity": "#,##,##0",
    "Turnover ₹":            "#,##,##0.00",
    "No. of Trades":         "#,##,##0",
    "Deliverable Qty":       "#,##,##0",
    "% Dly Qt to Traded Qty": "0.00",
    "DoD % (Traded Qty)":    "0.0",
    "DoD % (Turnover)":      "0.0",
    "DoD % (Deliverable Qty)": "0.0",
    "5D Avg %":              "0.00",
    "20D Avg %":             "0.00",
    "1Y Percentile":         "0.00",
}


def _export_frame(df: pd.DataFrame, show_analytics: bool) -> pd.DataFrame:
    cols = [
        "symbol", "series", "date_display",
        "traded_qty", "dod_traded_qty",
        "turnover", "dod_turnover",
        "trades",
        "deliverable_qty", "dod_deliverable_qty",
        "delivery_pct",
    ]
    if show_analytics:
        cols += ["delivery_pct_5d_avg", "delivery_pct_20d_avg",
                 "delivery_pct_percentile_1y", "signal"]
    cols = [c for c in cols if c in df.columns]
    return df[cols].rename(columns=_EXPORT_COLUMN_MAP)


def to_csv_bytes(df: pd.DataFrame, show_analytics: bool = True) -> bytes:
    return _export_frame(df, show_analytics).to_csv(index=False).encode("utf-8")


def to_excel_bytes(df: pd.DataFrame, show_analytics: bool = True) -> bytes:
    """Excel with Indian number formats and auto-width columns."""
    out = _export_frame(df, show_analytics)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        out.to_excel(writer, index=False, sheet_name="Delivery")
        ws = writer.sheets["Delivery"]
        for idx, col_name in enumerate(out.columns, start=1):
            letter = ws.cell(row=1, column=idx).column_letter
            fmt = _COL_FORMAT.get(col_name)
            if fmt:
                for cell in ws[letter][1:]:
                    cell.number_format = fmt
            max_len = len(str(col_name))
            for v in out[col_name].fillna("").astype(str).tolist()[:200]:
                if len(v) > max_len:
                    max_len = len(v)
            ws.column_dimensions[letter].width = min(max_len + 2, 40)
    return buf.getvalue()


# ============================================================================
# Cached loaders
# ============================================================================
@st.cache_data(ttl=600, show_spinner=False)
def cached_symbols() -> list[str]:
    return get_active_symbols()


# ============================================================================
# Footer
# ============================================================================
def render_footer() -> None:
    st.markdown(
        "<hr style='border:none;border-top:1px solid #1e2a3a;margin:24px 0 12px'/>"
        "<div style='text-align:center;color:#4a5a6a;font-size:0.8rem;padding:4px 0 16px'>"
        "Built by <strong style='color:#7a9abf'>Atharv Agrawal</strong>"
        "&nbsp;·&nbsp;"
        "<a href='https://www.linkedin.com/in/atharv-agrawal-295743233' "
        "target='_blank' style='color:#0a66c2;text-decoration:none;'>LinkedIn</a>"
        "</div>",
        unsafe_allow_html=True,
    )
