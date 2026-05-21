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
from st_aggrid import GridOptionsBuilder, JsCode

from config import DOD_THRESHOLDS
from universe import get_active_symbols


# ============================================================================
# AgGrid JS — exactly 3 reusable blocks
# ============================================================================
def build_dod_cell_style() -> JsCode:
    """Block 1: cellStyle_dod — generated from DOD_THRESHOLDS (no hardcoded hex)."""
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


# Block 2: valueFormatter_indian_int
INDIAN_INT_FORMATTER = JsCode("""
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

# Block 3: valueFormatter_indian_decimal
INDIAN_DECIMAL_FORMATTER = JsCode("""
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
    cell_style_dod = build_dod_cell_style()
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(sortable=True, filter=True, resizable=True)

    gb.configure_column("symbol",          header_name="Symbol", pinned="left", width=110)
    gb.configure_column("series",          header_name="Series", width=80)
    gb.configure_column("date_display",    header_name="Date", width=100)
    gb.configure_column("traded_qty",      header_name="Total Traded Qty",
                        valueFormatter=INDIAN_INT_FORMATTER, type=["numericColumn"], width=155)
    gb.configure_column("dod_traded_qty",  header_name="dod change",
                        cellStyle=cell_style_dod, type=["numericColumn"], width=105)
    gb.configure_column("turnover",        header_name="Turnover ₹",
                        valueFormatter=INDIAN_DECIMAL_FORMATTER, type=["numericColumn"], width=165)
    gb.configure_column("dod_turnover",    header_name="dod change",
                        cellStyle=cell_style_dod, type=["numericColumn"], width=105)
    gb.configure_column("trades",          header_name="No. of Trades",
                        valueFormatter=INDIAN_INT_FORMATTER, type=["numericColumn"], width=125)
    gb.configure_column("deliverable_qty", header_name="Deliverable Qty",
                        valueFormatter=INDIAN_INT_FORMATTER, type=["numericColumn"], width=145)
    gb.configure_column("dod_deliverable_qty", header_name="dod change",
                        cellStyle=cell_style_dod, type=["numericColumn"], width=105)
    gb.configure_column("delivery_pct",    header_name="% Dly Qt to Traded",
                        type=["numericColumn"], width=150)

    if show_analytics:
        gb.configure_column("delivery_pct_5d_avg",        header_name="5D Avg %",
                            type=["numericColumn"], width=100)
        gb.configure_column("delivery_pct_20d_avg",       header_name="20D Avg %",
                            type=["numericColumn"], width=100)
        gb.configure_column("delivery_pct_percentile_1y", header_name="1Y Pctile",
                            type=["numericColumn"], width=105)
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
    options["rowHeight"] = 24
    options["headerHeight"] = 36
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
            # auto-width — always convert to str to avoid float/NA issues
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
    st.divider()
    st.markdown(
        "<div style='text-align:center;color:#888;font-size:0.85rem;padding:8px 0 4px'>"
        "Built by <strong>Atharv Agrawal</strong> &nbsp;·&nbsp; "
        "<a href='https://www.linkedin.com/in/atharv-agrawal-295743233' "
        "target='_blank' style='color:#0a66c2;text-decoration:none;'>LinkedIn</a>"
        "</div>",
        unsafe_allow_html=True,
    )
