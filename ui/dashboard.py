"""Streamlit dashboard for the paper-trading bot.

Run: `uv run streamlit run ui/dashboard.py`. Dark theme via `.streamlit/config.toml`.
Reads SQLite decision log at `data/decision_log/traderbot.db` (override with env
`TRADERBOT_LOG_PATH`). Auto-refreshes every 10 seconds.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import os  # noqa: E402

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from backtest.compare import (  # noqa: E402
    DEFAULT_SYMBOLS,
    make_figure,
    run_comparison,
)
from memory.decision_log import DecisionLog  # noqa: E402
from risk.caps import DEFAULT_KILL_SWITCH_PATH, kill_switch_active  # noqa: E402
from tools import env_config, loop_control  # noqa: E402
from tools.notifier import TelegramNotifier  # noqa: E402
from ui.views import (  # noqa: E402
    day_pnl,
    equity_curve,
    event_counts,
    open_positions,
    soak_health,
    summary,
    trades_dataframe,
)

DEFAULT_DB_PATH = Path("data/decision_log/traderbot.db")
REFRESH_INTERVAL_S = 30

GREEN = "#22c55e"
RED = "#ef4444"
GREY = "#6b7280"
GOLD = "#fbbf24"

CSS = """
<style>
/* Institutional dark — single amber accent, mono numbers, no decoration. */
:root {
  --bg:        #0a0a0a;
  --card:      #111111;
  --border:    #1f1f1f;
  --border-2:  #2a2a2a;
  --text:      #e8e8e8;
  --text-2:    #a3a3a3;
  --text-3:    #737373;
  --accent:    #d97706;   /* amber, used sparingly */
  --green:     #16a34a;
  --red:       #dc2626;
}

.stApp { background: var(--bg) !important; }
header[data-testid="stHeader"] { background: transparent; }
.stDeployButton, footer { display: none; }
.block-container { padding-top: 2rem; padding-bottom: 1rem; max-width: 100%; }

/* --- Brand --- */
.brand {
  font-family: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 13px; font-weight: 700; letter-spacing: 0.18em;
  color: var(--text); text-transform: uppercase;
}
.brand .sep { color: var(--text-3); margin: 0 8px; font-weight: 400; }
.brand .sub { color: var(--text-3); font-size: 11px; font-weight: 500;
  letter-spacing: 0.14em; margin-left: 10px; }

/* --- Mode tag (PAPER / LIVE) — sober, no glow, no pulse --- */
.mode {
  display: inline-block; margin-left: 12px;
  padding: 2px 8px;
  font-family: "SF Mono", Menlo, monospace;
  font-size: 10px; font-weight: 700; letter-spacing: 0.14em;
  border: 1px solid;
  vertical-align: 1px;
}
.mode.paper { color: var(--text-2); border-color: var(--border-2); }
.mode.live  { color: #fff; background: var(--red); border-color: var(--red); }

/* --- Status text (RUN / IDLE / OFF) — text, not dot --- */
.status {
  font-family: "SF Mono", Menlo, monospace;
  font-size: 11px; letter-spacing: 0.10em;
  color: var(--text-2);
}
.status .v { color: var(--text); font-weight: 600; }
.status .v.run  { color: var(--green); }
.status .v.idle { color: var(--accent); }
.status .v.off  { color: var(--red); }

/* --- KPI cards — flat, dense, mono --- */
.kpi {
  background: var(--card);
  border: 1px solid var(--border);
  padding: 12px 14px;
  height: 100%;
}
.kpi .label {
  font-family: "Inter", sans-serif;
  font-size: 9px; color: var(--text-3);
  text-transform: uppercase; letter-spacing: 0.16em;
  margin-bottom: 10px;
}
.kpi .value {
  font-family: "SF Mono", Menlo, monospace;
  font-size: 22px; font-weight: 600; line-height: 1.0;
  color: var(--text); letter-spacing: -0.01em;
  font-variant-numeric: tabular-nums;
}
.kpi .delta {
  font-family: "SF Mono", Menlo, monospace;
  font-size: 11px; color: var(--text-3);
  margin-top: 6px; letter-spacing: 0;
}
.kpi.pos .value { color: var(--green); }
.kpi.neg .value { color: var(--red); }
.kpi.pos .delta { color: var(--green); }
.kpi.neg .delta { color: var(--red); }

/* --- Section headers (no native container wrapping — too fragile in Streamlit) --- */
.sect {
  display: flex; justify-content: space-between; align-items: baseline;
  margin: 18px 0 8px 0; padding: 0 0 6px 0;
  border-bottom: 1px solid var(--border);
}
.sect .t {
  font-family: "Inter", sans-serif;
  font-size: 10px; color: var(--text-2);
  text-transform: uppercase; letter-spacing: 0.16em;
  font-weight: 700;
}
.sect .r {
  font-family: "SF Mono", Menlo, monospace;
  font-size: 10px; color: var(--text-3); letter-spacing: 0.04em;
}

/* --- Action tags --- */
.act {
  display: inline-block; padding: 1px 6px;
  font-family: "SF Mono", Menlo, monospace;
  font-size: 10px; font-weight: 700; letter-spacing: 0.08em;
  border: 1px solid;
}
.act-buy   { color: var(--green); border-color: rgba(22,163,74,0.4); }
.act-close { color: var(--red);   border-color: rgba(220,38,38,0.4); }

/* --- Tables --- */
.t {
  width: 100%; border-collapse: collapse;
  font-family: "SF Mono", Menlo, monospace;
  font-size: 11px;
}
.t thead th {
  font-family: "Inter", sans-serif;
  font-size: 9px; color: var(--text-3);
  text-transform: uppercase; letter-spacing: 0.12em;
  text-align: left; padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  font-weight: 600;
}
.t tbody td {
  padding: 7px 12px; border-bottom: 1px solid var(--border);
  color: var(--text);
}
.t tbody tr:hover { background: #161616; }
.t .num { text-align: right; font-variant-numeric: tabular-nums; }
.t .pos { color: var(--green); }
.t .neg { color: var(--red); }
.t .muted { color: var(--text-3); }

/* --- Activity feed (log) --- */
.feed {
  background: var(--bg);
  font-family: "SF Mono", Menlo, monospace;
  font-size: 11px; line-height: 1.55;
  padding: 8px 12px;
  max-height: 380px; overflow-y: auto;
  color: var(--text-2);
}
.feed .ts   { color: var(--text-3); }
.feed .ok   { color: var(--green); }
.feed .err  { color: var(--red); }
.feed .warn { color: var(--accent); }
.feed .sig  { color: var(--text-2); }

/* --- Footer --- */
.footer {
  margin-top: 32px; padding: 12px 12px;
  border-top: 1px solid var(--border);
  display: flex; justify-content: space-between;
  font-family: "SF Mono", Menlo, monospace;
  font-size: 10px; color: var(--text-3);
  letter-spacing: 0.10em; text-transform: uppercase;
  clear: both;
}

/* Streamlit overrides */
.muted { color: var(--text-3); font-size: 11px; }
.section-title {
  font-family: "Inter", sans-serif;
  font-size: 9px; color: var(--text-2); text-transform: uppercase;
  letter-spacing: 0.16em; font-weight: 600;
  margin: 14px 0 6px 0; padding-bottom: 4px;
  border-bottom: 1px solid var(--border);
}

/* Streamlit tabs — neutralize colors */
button[data-baseweb="tab"] {
  font-family: "Inter", sans-serif !important;
  font-size: 11px !important; letter-spacing: 0.10em !important;
  text-transform: uppercase !important; font-weight: 600 !important;
  color: var(--text-3) !important;
}
button[data-baseweb="tab"][aria-selected="true"] { color: var(--text) !important; }
div[data-baseweb="tab-highlight"] { background: var(--accent) !important; }
</style>
"""


def _ts_to_str(ts_ms: int, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return pd.to_datetime(ts_ms, unit="ms", utc=True).strftime(fmt)


def _fmt_money(v: float, sign: bool = False) -> str:
    s = f"{v:+,.2f}" if sign else f"{v:,.2f}"
    return f"${s}"


def _load_buy_hold_curve(rows: list[dict], initial_cash: float) -> pd.DataFrame:
    """Build a buy-and-hold equity curve from the parquet that matches the soak window.

    Uses the symbol of the first fill + the first/last fill timestamps to slice the
    parquet. Returns empty DataFrame if no parquet or no fills.
    """
    fills = [r for r in rows if r["event_type"] == "order_filled"]
    if not fills:
        return pd.DataFrame(columns=["timestamp_ms", "equity"])
    symbol = fills[0]["symbol"]
    from data.store import bars_path, load_bars  # noqa: PLC0415

    path = bars_path("binance", symbol, "1h")
    if not path.exists():
        return pd.DataFrame(columns=["timestamp_ms", "equity"])
    bars = load_bars(path)
    if not bars:
        return pd.DataFrame(columns=["timestamp_ms", "equity"])
    df = pd.DataFrame(bars)
    start_ts = min(int(r["timestamp_ms"]) for r in fills)
    end_ts = max(int(r["timestamp_ms"]) for r in fills)
    df = df[(df["timestamp_ms"] >= start_ts) & (df["timestamp_ms"] <= end_ts)]
    if df.empty:
        return pd.DataFrame(columns=["timestamp_ms", "equity"])
    base = float(df["close"].iloc[0])
    return pd.DataFrame(
        {
            "timestamp_ms": df["timestamp_ms"].astype(int),
            "equity": initial_cash * df["close"].astype(float) / base,
        }
    )


def _equity_chart(
    eq_df: pd.DataFrame, initial_cash: float, rows: list[dict] | None = None
) -> go.Figure:
    fig = go.Figure()
    if eq_df.empty:
        # Anchor at initial_cash so the chart reads "flat at start" rather than
        # the misleading Plotly auto-axis (-1 to 3) that defaults when no traces.
        fig.add_hline(
            y=initial_cash,
            line={"color": "#737373", "width": 1, "dash": "dot"},
            annotation_text=f"Start ${initial_cash:,.0f}",
            annotation_position="top right",
            annotation_font_color="#737373",
            annotation_font_size=10,
        )
        fig.add_annotation(
            text="Waiting for the first trade — no fills yet.",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font={"color": "#737373", "size": 13},
        )
        fig.update_xaxes(visible=False)
        fig.update_yaxes(
            range=[initial_cash * 0.95, initial_cash * 1.05],
            tickprefix="$",
            tickformat=",.0f",
            gridcolor="#1f1f1f",
        )
    else:
        ts = pd.to_datetime(eq_df["timestamp_ms"], unit="ms", utc=True)

        # Buy-and-hold benchmark (only when we have fills + parquet for the symbol)
        if rows is not None:
            bh = _load_buy_hold_curve(rows, initial_cash)
            if not bh.empty:
                fig.add_trace(
                    go.Scatter(
                        x=pd.to_datetime(bh["timestamp_ms"], unit="ms", utc=True),
                        y=bh["equity"],
                        mode="lines",
                        line={"color": "#6b7280", "width": 1.5, "dash": "dot"},
                        name="Buy &amp; hold",
                        hovertemplate="B&amp;H %{y:$,.2f}<extra></extra>",
                    )
                )

        # Strategy equity (step line)
        fig.add_trace(
            go.Scatter(
                x=ts,
                y=eq_df["equity"],
                mode="lines",
                line={"color": "#d97706", "width": 1.8, "shape": "hv"},
                name="Strategy",
                hovertemplate="Strat %{y:$,.2f}<extra></extra>",
            )
        )

        # Trade markers — diamond at each fill, color by side
        if rows is not None:
            fills = [r for r in rows if r["event_type"] == "order_filled"]
            if fills:
                buys = [f for f in fills if f["side"] == "buy"]
                sells = [f for f in fills if f["side"] == "sell"]
                eq_by_ts = dict(zip(eq_df["timestamp_ms"], eq_df["equity"], strict=False))
                if buys:
                    bx = pd.to_datetime([int(f["timestamp_ms"]) for f in buys], unit="ms", utc=True)
                    by = [eq_by_ts.get(int(f["timestamp_ms"]), float("nan")) for f in buys]
                    fig.add_trace(
                        go.Scatter(
                            x=bx,
                            y=by,
                            mode="markers",
                            marker={"color": GREEN, "size": 7, "symbol": "triangle-up"},
                            name="Buy",
                            hovertemplate="BUY @ %{x|%m-%d %H:%M}<extra></extra>",
                        )
                    )
                if sells:
                    sx = pd.to_datetime(
                        [int(f["timestamp_ms"]) for f in sells], unit="ms", utc=True
                    )
                    sy = [eq_by_ts.get(int(f["timestamp_ms"]), float("nan")) for f in sells]
                    fig.add_trace(
                        go.Scatter(
                            x=sx,
                            y=sy,
                            mode="markers",
                            marker={"color": RED, "size": 7, "symbol": "triangle-down"},
                            name="Sell",
                            hovertemplate="SELL @ %{x|%m-%d %H:%M}<extra></extra>",
                        )
                    )

        fig.add_hline(
            y=initial_cash,
            line={"color": "#374151", "width": 1, "dash": "dot"},
            annotation_text="Start",
            annotation_position="top right",
            annotation_font_color="#6b7280",
            annotation_font_size=10,
        )

    fig.update_layout(
        height=300,
        margin={"l": 4, "r": 4, "t": 4, "b": 4},
        paper_bgcolor="#111111",
        plot_bgcolor="#111111",
        xaxis={
            "gridcolor": "#1f2937",
            "showspikes": True,
            "spikemode": "across",
            "spikecolor": "#374151",
            "spikethickness": 1,
        },
        # autorange=True + tight padding lets the chart zoom to the actual range,
        # not a forced [$0, max] which makes small moves invisible.
        yaxis={
            "gridcolor": "#1f2937",
            "tickprefix": "$",
            "tickformat": ",.0f",
            "autorange": True,
            "fixedrange": False,
        },
        showlegend=True,
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "x": 0,
            "bgcolor": "rgba(0,0,0,0)",
            "font": {"size": 10, "color": "#9ca3af"},
        },
        hovermode="x unified",
    )
    return fig


def _badge(label: str, kind: str) -> str:
    return f'<span class="badge badge-{kind}">{label}</span>'


def _trades_table_html(trades: pd.DataFrame, fills: pd.DataFrame | None = None) -> str:
    """Reference-style: TIME · ACTION badge · SYMBOL · QTY · DETAILS.

    DETAILS encodes everything secondary in one column: SL/TP for opens, exit-reason
    + P&L % for closes. Compact + scannable like the reference image.
    """
    if trades.empty and (fills is None or fills.empty):
        return '<div class="muted" style="padding:14px;">No trades or fills yet.</div>'

    items: list[tuple[int, str, str, float, str]] = []
    for _, t in trades.tail(60).iterrows():
        pnl = float(t["pnl"])
        ret = float(t["return_pct"]) * 100
        reason = (t["exit_reason"] or "exit").replace("_", "-")
        details = (
            f"{reason} · ${float(t['exit_price']):,.2f} "
            f'<span class="{"pos" if pnl >= 0 else "neg"}">P&amp;L {pnl:+,.2f} ({ret:+.2f}%)</span>'
        )
        items.append((int(t["exit_ts"]), "CLOSE", "BTC/USDT", float(t["qty"]), details))
        details_buy = (
            f"SL ${float(t['entry_price']) * 0.97:,.2f} / TP ${float(t['entry_price']) * 1.04:,.2f}"
        )
        items.append((int(t["entry_ts"]), "BUY", "BTC/USDT", float(t["qty"]), details_buy))

    items.sort(key=lambda x: -x[0])
    items = items[:25]

    rows_html: list[str] = []
    for ts, action, sym, qty, details in items:
        act_cls = "act-buy" if action == "BUY" else "act-close"
        rows_html.append(
            f"<tr>"
            f'<td class="muted">{_ts_to_str(ts, "%m-%d %H:%M")}</td>'
            f'<td><span class="act {act_cls}">{action}</span></td>'
            f"<td><strong>{sym}</strong></td>"
            f'<td class="num">{qty:.6f}</td>'
            f'<td class="muted">{details}</td>'
            f"</tr>"
        )
    return (
        '<table class="t">'
        "<thead><tr>"
        "<th>TIME</th><th>ACTION</th><th>SYMBOL</th>"
        '<th class="num">QTY</th><th>DETAILS</th>'
        "</tr></thead>"
        "<tbody>" + "".join(rows_html) + "</tbody></table>"
    )


def _live_log_html(rows: list[dict], n: int = 30) -> str:
    """Recent decision-log lines, color-coded by event type."""
    if not rows:
        return '<div class="muted">No log entries yet.</div>'
    lines: list[str] = []
    for r in rows[-n:][::-1]:
        ts = _ts_to_str(int(r["timestamp_ms"]), "%H:%M:%S")
        ev = r["event_type"]
        sym = r["symbol"]
        side = r["side"] or ""
        rationale = (r["rationale"] or "")[:60]
        cls = {
            "order_filled": "log-buy" if side == "buy" else "log-sell",
            "order_placed": "log-sig",
            "signal": "log-sig",
            "risk_block": "log-block",
            "order_rejected": "log-block",
        }.get(ev, "")
        if ev == "order_filled":
            qty = r.get("quantity") or 0
            px = r.get("price") or 0
            txt = f"FILL {side.upper():4s} {sym} qty={qty:.4f} @ {px:,.2f}"
        elif ev == "order_placed":
            qty = r.get("quantity") or 0
            txt = f"PLACE {side.upper():4s} {sym} qty={qty:.4f}"
        elif ev == "risk_block":
            txt = f"BLOCK {sym} reason={rationale}"
        elif ev == "signal" and side != "hold":
            txt = f"SIG  {side.upper():4s} {sym} {rationale[:40]}"
        else:
            continue  # skip hold-signals from live log to reduce noise
        lines.append(
            f'<div class="log-line"><span class="log-time">{ts}</span> '
            f'<span class="{cls}">{txt}</span></div>'
        )
    if not lines:
        return '<div class="muted">No actionable events yet — only hold signals.</div>'
    return (
        '<div style="background:#0b0f17; border:1px solid #1f2937; border-radius:6px; '
        'padding:8px 12px; max-height:340px; overflow-y:auto;">' + "".join(lines) + "</div>"
    )


def _render_compare_tab() -> None:
    """Backtest comparison — runs `backtest.compare` in-process and shows the chart."""
    st.markdown(
        '<div class="muted" style="margin-bottom:8px;">Run the baseline EMA-cross strategy '
        "across multiple symbols and compare equity curves vs buy-and-hold. "
        "Backfills are reused if the local parquet already covers the window.</div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns([3, 1, 1])
    symbols_str = c1.text_input(
        "Symbols (comma-separated)",
        ", ".join(DEFAULT_SYMBOLS),
        key="compare_symbols",
    )
    days = c2.number_input("Days", min_value=7, max_value=180, value=30, step=1, key="compare_days")
    timeframe = c3.selectbox("Timeframe", ["1h", "4h", "1d"], index=0, key="compare_tf")

    if st.button("Run comparison", type="primary"):
        symbols = [s.strip() for s in symbols_str.split(",") if s.strip()]
        if not symbols:
            st.error("Need at least one symbol.")
            return
        with st.spinner(f"Backfilling + backtesting {len(symbols)} symbols..."):
            try:
                results = run_comparison(symbols, days=int(days), timeframe=timeframe)
            except Exception as e:
                st.error(f"Comparison failed: {type(e).__name__}: {e}")
                return
        st.session_state["compare_results"] = results

    results = st.session_state.get("compare_results")
    if results is None:
        st.info("Set parameters and click **Run comparison**.")
        return

    st.plotly_chart(make_figure(results), config={"displayModeBar": False}, width="stretch")

    st.markdown('<div class="section-title">Per-symbol breakdown</div>', unsafe_allow_html=True)
    rows_html: list[str] = []
    for r in results:
        m = r.result.metrics
        strat_pct = m["total_return_pct"] * 100
        diff = strat_pct - r.buy_hold_return_pct
        diff_color = GREEN if diff > 0 else (RED if diff < 0 else GREY)
        strat_color = GREEN if strat_pct > 0 else (RED if strat_pct < 0 else GREY)
        rows_html.append(
            f'<tr style="border-bottom:1px solid #1f2937;">'
            f'<td style="padding:6px 8px;"><strong>{r.symbol}</strong></td>'
            f'<td style="padding:6px 8px; text-align:right;">{r.bars}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{int(m["num_trades"])}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{m["win_rate"] * 100:.1f}%</td>'
            f'<td style="padding:6px 8px; text-align:right; color:{strat_color}; '
            f'font-weight:600;">{strat_pct:+.2f}%</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r.buy_hold_return_pct:+.2f}%</td>'
            f'<td style="padding:6px 8px; text-align:right; color:{diff_color};">'
            f"{diff:+.2f}pp</td>"
            f'<td style="padding:6px 8px; text-align:right;">{m["sharpe"]:+.2f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{m["max_drawdown"] * 100:.2f}%</td>'
            f"</tr>"
        )
    st.markdown(
        '<table style="width:100%; border-collapse:collapse; font-size:12px;">'
        '<thead><tr style="color:#9ca3af; font-size:10px; text-transform:uppercase; '
        'letter-spacing:0.08em; border-bottom:1px solid #374151;">'
        '<th style="padding:6px 8px; text-align:left;">Symbol</th>'
        '<th style="padding:6px 8px; text-align:right;">Bars</th>'
        '<th style="padding:6px 8px; text-align:right;">Trades</th>'
        '<th style="padding:6px 8px; text-align:right;">WR</th>'
        '<th style="padding:6px 8px; text-align:right;">Strategy</th>'
        '<th style="padding:6px 8px; text-align:right;">B&amp;H</th>'
        '<th style="padding:6px 8px; text-align:right;">Diff</th>'
        '<th style="padding:6px 8px; text-align:right;">Sharpe</th>'
        '<th style="padding:6px 8px; text-align:right;">MaxDD</th>'
        "</tr></thead><tbody>" + "".join(rows_html) + "</tbody></table>",
        unsafe_allow_html=True,
    )


def _positions_html(positions: list[dict]) -> str:
    if not positions:
        return '<div class="muted" style="padding:14px;">No open positions.</div>'
    rows_html: list[str] = []
    for p in positions:
        upnl = float(p["unrealized_pnl"])
        avg = float(p["avg_entry"])
        last = float(p["last_price"])
        upnl_pct = (last / avg - 1.0) * 100 if avg > 0 else 0.0
        cls = "pos" if upnl > 0 else ("neg" if upnl < 0 else "muted")
        rows_html.append(
            f"<tr>"
            f"<td><strong>{p['symbol']}</strong></td>"
            f'<td class="num">{p["qty"]:.4f}</td>'
            f'<td class="num">${avg:,.2f}</td>'
            f'<td class="num">${last:,.2f}</td>'
            f'<td class="num {cls}">${upnl:+,.2f}<br>'
            f'<span style="font-size:10px;">{upnl_pct:+.2f}%</span></td>'
            f"</tr>"
        )
    return (
        '<table class="t">'
        "<thead><tr>"
        "<th>SYMBOL</th>"
        '<th class="num">QTY</th>'
        '<th class="num">ENTRY</th>'
        '<th class="num">CURRENT</th>'
        '<th class="num">P&amp;L</th>'
        "</tr></thead>"
        "<tbody>" + "".join(rows_html) + "</tbody></table>"
    )


def render(log_path: Path, initial_cash: float, kill_switch_path: Path) -> None:
    st.set_page_config(page_title="traderbot", layout="wide", initial_sidebar_state="expanded")
    st.markdown(CSS, unsafe_allow_html=True)
    # Real auto-refresh — header text claimed it; this makes it true.
    st.markdown(
        f"<script>setTimeout(function(){{window.parent.location.reload();}}, "
        f"{REFRESH_INTERVAL_S * 1000});</script>",
        unsafe_allow_html=True,
    )

    rows: list[dict] = []
    if log_path.exists():
        log = DecisionLog(log_path)
        rows = log.all()
        log.close()

    s = summary(rows, initial_cash=initial_cash)
    eq_df = equity_curve(rows, initial_cash=initial_cash)
    positions = open_positions(rows)

    current_equity = float(eq_df.iloc[-1]["equity"]) if not eq_df.empty else initial_cash
    current_cash = float(eq_df.iloc[-1]["cash"]) if not eq_df.empty else initial_cash
    total_return = (current_equity - initial_cash) / initial_cash if initial_cash > 0 else 0

    # --- Header: institutional + trader context ---
    now = pd.Timestamp.utcnow()
    now_utc_str = now.strftime("%H:%M:%S UTC")
    loop_alive = loop_control.status().running
    kill_on = kill_switch_active(kill_switch_path)
    live_trading = os.environ.get("LIVE_TRADING", "false").strip().lower() == "true"
    if loop_alive and not kill_on:
        status_v, status_cls = "RUN", "run"
    elif kill_on:
        status_v, status_cls = "IDLE", "idle"
    else:
        status_v, status_cls = "OFF", "off"
    mode_cls = "live" if live_trading else "paper"
    mode_text = "LIVE" if live_trading else "PAPER"

    # Symbol + timeframe + countdown to next bar close (1h-bar trader needs this).
    symbol_env = os.environ.get("TRADERBOT_SYMBOL", "BTC/USDT")
    timeframe = os.environ.get("TRADERBOT_TIMEFRAME", "1h")
    tf_seconds = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }.get(timeframe, 3600)
    next_bar_in = tf_seconds - (int(now.timestamp()) % tf_seconds)
    nb_min, nb_sec = divmod(next_bar_in, 60)
    nb_h, nb_min = divmod(nb_min, 60)
    next_bar_str = f"{nb_h}h {nb_min:02d}m" if nb_h else f"{nb_min:02d}:{nb_sec:02d}"

    st.markdown(
        f'<div style="display:flex; justify-content:space-between; align-items:center; '
        f"padding-bottom:10px; border-bottom:1px solid #1f1f1f; margin-bottom:14px; "
        f'flex-wrap:wrap; gap:10px;">'
        f'<div class="brand">WOLF OF VIBE STREET'
        f'<span class="sub">DASHBOARD</span>'
        f'<span class="mode {mode_cls}">{mode_text}</span></div>'
        f'<div class="status" style="display:flex; gap:14px; flex-wrap:wrap;">'
        f'<span><span style="color:#737373;">SYMBOL</span> '
        f'<span class="v">{symbol_env} · {timeframe}</span></span>'
        f'<span><span style="color:#737373;">NEXT BAR</span> '
        f'<span class="v">{next_bar_str}</span></span>'
        f'<span class="v">{now_utc_str}</span>'
        f'<span><span style="color:#737373;">STATUS</span> '
        f'<span class="v {status_cls}">{status_v}</span></span>'
        f"</div></div>",
        unsafe_allow_html=True,
    )

    if not rows:
        st.warning(
            f"No decision log yet at `{log_path}`. Start the live loop "
            f"(`uv run python -m workers.live_loop`) — page auto-refreshes."
        )

    tab_overview, tab_compare = st.tabs(["OVERVIEW", "BACKTEST COMPARE"])

    with tab_overview:
        # Soak health — green/yellow/red banner so a morning check is one glance.
        loop_status = loop_control.status()
        checks = soak_health(
            rows,
            bot_running=loop_status.running,
            kill_switch_on=kill_switch_active(kill_switch_path),
            now_ms=int(pd.Timestamp.utcnow().timestamp() * 1000),
        )
        worst = "ok"
        for c in checks:
            if c["status"] == "error":
                worst = "error"
                break
            if c["status"] == "warn" and worst != "error":
                worst = "warn"
        banner_color = {"ok": "#16a34a", "warn": "#d97706", "error": "#dc2626"}[worst]
        banner_label = {"ok": "HEALTHY", "warn": "ATTENTION", "error": "ISSUES"}[worst]
        st.markdown(
            f'<div style="background:#111111; border:1px solid #1f1f1f; '
            f"border-left:2px solid {banner_color}; "
            f'padding:10px 14px; margin-bottom:10px;">'
            f'<div style="display:flex; justify-content:space-between; align-items:center;">'
            f'<span style="font-family:Inter,sans-serif; font-size:9px; color:#a3a3a3; '
            f'text-transform:uppercase; letter-spacing:0.16em; font-weight:600;">'
            f"Soak status</span>"
            f"<span style=\"font-family:'SF Mono',Menlo,monospace; "
            f"color:{banner_color}; font-weight:700; font-size:11px; "
            f'letter-spacing:0.14em;">{banner_label}</span></div>'
            f'<div style="margin-top:8px; display:grid; '
            f"grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:4px 16px; "
            f"font-family:'SF Mono',Menlo,monospace; font-size:10px;\">"
            + "".join(
                '<div><span style="color:'
                + {"ok": "#16a34a", "warn": "#d97706", "error": "#dc2626"}[c["status"]]
                + '; font-weight:700;">['
                + c["status"].upper()[:3]
                + "]</span> "
                f'<span style="color:#e8e8e8;">{c["name"]}</span> '
                f'<span style="color:#737373;">— {c["message"]}</span></div>'
                for c in checks
            )
            + "</div></div>",
            unsafe_allow_html=True,
        )

        # --- 5 KPI cards with colored top-borders, matches reference ---
        from risk.caps import RiskCaps as _RiskCaps  # noqa: PLC0415

        max_pos = _RiskCaps().max_concurrent_positions
        now_ms = int(pd.Timestamp.utcnow().timestamp() * 1000)
        today_pnl = day_pnl(rows, now_ms=now_ms)

        def _sign(value: float) -> str:
            if value > 0:
                return "pos"
            if value < 0:
                return "neg"
            return ""

        def _kpi(state: str, label: str, value: str, delta: str = "") -> str:
            delta_html = f'<div class="delta">{delta}</div>' if delta else ""
            return (
                f'<div class="kpi {state}">'
                f'<div class="label">{label}</div>'
                f'<div class="value">{value}</div>'
                f"{delta_html}</div>"
            )

        k1, k2, k3, k4, k5 = st.columns(5)
        delta_eq = current_equity - initial_cash
        k1.markdown(
            _kpi(
                _sign(delta_eq), "Equity", f"${current_equity:,.2f}", f"{total_return * 100:+.2f}%"
            ),
            unsafe_allow_html=True,
        )
        k2.markdown(
            _kpi(
                _sign(current_cash - initial_cash),
                "Cash",
                f"${current_cash:,.2f}",
                f"{(current_cash / initial_cash) * 100:.1f}% of start",
            ),
            unsafe_allow_html=True,
        )
        k3.markdown(
            _kpi(
                _sign(today_pnl),
                "Day P&L (today UTC)",
                f"${today_pnl:+,.2f}" if today_pnl else "$0.00",
                "",
            ),
            unsafe_allow_html=True,
        )
        k4.markdown(
            _kpi(
                _sign(delta_eq),
                "Total P&L (since start)",
                f"${delta_eq:+,.2f}",
                f"{total_return * 100:+.2f}%",
            ),
            unsafe_allow_html=True,
        )
        k5.markdown(
            _kpi("", "Positions", str(len(positions)), f"max {max_pos}"),
            unsafe_allow_html=True,
        )

        # --- Performance metrics row (denser, no labels above values) ---
        from backtest.metrics import equity_returns, max_drawdown, sharpe  # noqa: PLC0415

        # n=0 → "—", not 0/red. Sharpe/Max DD only meaningful with multi-bar history.
        n_trades = s["trades"]
        sharpe_v: float | None = None
        maxdd_v: float | None = None
        if not eq_df.empty and len(eq_df) >= 2:
            eq_series = pd.Series(eq_df["equity"].to_numpy())
            rets = equity_returns(eq_series)
            if len(rets) >= 2:
                sharpe_v = sharpe(rets)
                maxdd_v = max_drawdown(eq_series)
        win_rate_v: float | None = (s["win_rate"] * 100) if n_trades > 0 else None
        exposure_pct = (
            sum(p["last_price"] * p["qty"] for p in positions) / current_equity * 100
            if current_equity > 0
            else 0.0
        )

        neutral = "#a3a3a3"

        def _mini(label: str, value: str, color: str = "#e8e8e8") -> str:
            # Subordinate to KPI cards: smaller value (16px), tighter padding.
            return (
                f'<div style="background:#111111; border:1px solid #1f1f1f; '
                f'padding:8px 12px;">'
                f'<div style="font-family:Inter,sans-serif; font-size:9px; color:#737373; '
                f'text-transform:uppercase; letter-spacing:0.14em; margin-bottom:4px;">'
                f"{label}</div>"
                f"<div style=\"font-family:'SF Mono',Menlo,monospace; font-size:16px; "
                f'font-weight:600; color:{color}; line-height:1;">{value}</div></div>'
            )

        if sharpe_v is None:
            sharpe_str, sharpe_color = "—", neutral
        else:
            sharpe_str = f"{sharpe_v:+.2f}"
            sharpe_color = "#16a34a" if sharpe_v > 0 else ("#dc2626" if sharpe_v < 0 else "#e8e8e8")
        if maxdd_v is None:
            maxdd_str, maxdd_color = "—", neutral
        elif maxdd_v == 0:
            maxdd_str, maxdd_color = "0.00%", neutral
        else:
            maxdd_str, maxdd_color = f"{maxdd_v * 100:.2f}%", "#dc2626"
        if win_rate_v is None:
            wr_str, wr_color = "—", neutral
        else:
            wr_str = f"{win_rate_v:.1f}%"
            wr_color = "#16a34a" if win_rate_v >= 33.3 else "#dc2626"
        # Exposure: always show "X% / 100% cap" so the cap is visible context.
        if exposure_pct == 0:
            exp_str, exp_color = "0% / 100% cap", neutral
        else:
            exp_str, exp_color = f"{exposure_pct:.1f}% / 100% cap", "#d97706"

        m1, m2, m3, m4 = st.columns(4)
        m1.markdown(_mini("Sharpe (ann.)", sharpe_str, sharpe_color), unsafe_allow_html=True)
        m2.markdown(_mini("Max drawdown", maxdd_str, maxdd_color), unsafe_allow_html=True)
        m3.markdown(_mini("Win rate", wr_str, wr_color), unsafe_allow_html=True)
        m4.markdown(
            _mini("Exposure", exp_str, exp_color),
        )

        st.write("")  # spacer

        # Section header pattern: title left + right annotation, border below.
        # Bullet-proof — single markdown block, no Streamlit-component containment
        # tricks. Sections flow vertically; column splits handle horizontal layout.
        def _section(title: str, right: str = "") -> None:
            r = f'<span class="r">{right}</span>' if right else ""
            st.markdown(
                f'<div class="sect"><span class="t">{title}</span>{r}</div>',
                unsafe_allow_html=True,
            )

        # --- Row 1: Equity curve (left, 2x) + Open positions (right, 1x) ---
        left, right = st.columns([2, 1])
        with left:
            _section("Equity curve", f"{total_return * 100:+.2f}% total")
            st.plotly_chart(
                _equity_chart(eq_df, initial_cash, rows=rows),
                config={"displayModeBar": False},
                width="stretch",
            )
        with right:
            _section("Open positions", f"{len(positions)} / {max_pos}")
            st.markdown(_positions_html(positions), unsafe_allow_html=True)

        # --- Row 2: Trade history (left, 2x) + Activity feed (right, 1x) ---
        left2, right2 = st.columns([2, 1])
        with left2:
            _section("Trade history", "last 25")
            st.markdown(_trades_table_html(trades_dataframe(rows)), unsafe_allow_html=True)

        with right2:
            _section("Activity feed", "decisions · loop stdout")
            tab_dec, tab_stdout = st.tabs(["DECISIONS", "LOOP STDOUT"])
            with tab_dec:
                st.markdown(_live_log_html(rows, n=80), unsafe_allow_html=True)
            with tab_stdout:
                loop_log_text = loop_control.tail_log(lines=80) or (
                    "(no loop log yet — start the loop from the sidebar)"
                )
                st.code(loop_log_text, language="bash")

            if s["blocks_by_reason"]:
                _section("Risk blocks")
                for reason, count in sorted(s["blocks_by_reason"].items(), key=lambda kv: -kv[1]):
                    st.markdown(
                        f'<div style="display:flex; justify-content:space-between; '
                        f"padding:4px 0; font-family:'SF Mono',Menlo,monospace; "
                        f'font-size:11px;">'
                        f'<span style="color:#d97706;">{reason}</span>'
                        f'<span style="color:#737373;">{count}</span></div>',
                        unsafe_allow_html=True,
                    )

        # --- Footer disclaimer ---
        last_refresh = pd.Timestamp.utcnow().strftime("%H:%M:%S UTC")
        st.markdown(
            f'<div class="footer">'
            f"<div>PAPER TRADING // FOR TESTING ONLY // NOT FINANCIAL ADVICE</div>"
            f"<div>Last refresh: {last_refresh}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    with tab_compare:
        _render_compare_tab()

    with st.sidebar:
        st.subheader("LIVE LOOP")
        loop_status = loop_control.status()
        if loop_status.running:
            uptime_s = (
                int((pd.Timestamp.utcnow().timestamp() * 1000 - loop_status.started_at_ms) / 1000)
                if loop_status.started_at_ms
                else 0
            )
            mins, secs = divmod(uptime_s, 60)
            hours, mins = divmod(mins, 60)
            st.success(
                f"Running · PID {loop_status.pid} · uptime {hours:d}h {mins:02d}m {secs:02d}s"
            )
            if st.button("Stop loop", type="secondary", width="stretch"):
                with st.spinner("Stopping..."):
                    loop_control.stop()
                st.rerun()
        else:
            st.warning("Loop not running")
            if st.button("Start loop", type="primary", width="stretch"):
                with st.spinner("Starting (caffeinate + uv)..."):
                    new_status = loop_control.start()
                if not new_status.running:
                    st.error("Loop failed to start — check log below.")
                st.rerun()
        st.caption(f"Logs: `{loop_status.log_path}`")

        with st.expander("RESET FOR FRESH SOAK", expanded=False):
            st.caption(
                "Wipes the decision log so the dashboard shows ONLY data from this point forward. "
                "Old log is moved to `data/decision_log/backups/` (you can always restore it). "
                "Stops the loop first; you'll need to start it again after."
            )
            confirm = st.checkbox("Yes, I want to start with a clean log", key="reset_confirm")
            if st.button(
                "Reset decision log",
                type="secondary",
                width="stretch",
                disabled=not confirm,
            ):
                if loop_control.status().running:
                    with st.spinner("Stopping loop..."):
                        loop_control.stop()
                backup = loop_control.reset_decision_log(log_path)
                if backup:
                    st.success(f"Log reset. Backup: `{backup}`")
                else:
                    st.info("Nothing to reset — log was already empty.")
                # Clear the checkbox state so it has to be re-checked next time.
                st.session_state.pop("reset_confirm", None)
                st.rerun()

        st.divider()
        st.subheader("KILL SWITCH")
        if kill_switch_active(kill_switch_path):
            st.error("ACTIVE — bot is paused")
            if st.button("Disable kill switch", type="primary", width="stretch"):
                if kill_switch_path.exists():
                    kill_switch_path.unlink()
                st.rerun()
        else:
            st.success("OFF — bot may trade")
            if st.button("Enable kill switch", type="secondary", width="stretch"):
                kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
                kill_switch_path.touch()
                st.rerun()
        st.caption(f"File: `{kill_switch_path}`")

        st.divider()
        with st.expander("TELEGRAM ALERTS", expanded=False):
            env = env_config.read_env()
            current_token = env.get("TELEGRAM_BOT_TOKEN", "")
            current_chat = env.get("TELEGRAM_CHAT_ID", "")
            configured = bool(current_token and current_chat)
            if configured:
                st.success("Configured ✓")
            else:
                st.warning("Not configured")
            st.caption(
                "How to set up: write to @BotFather on Telegram → /newbot → copy the token. "
                "Get your chat ID by writing to @userinfobot. Paste both below."
            )
            token_in = st.text_input(
                "Bot token", value=current_token, type="password", key="tg_token"
            )
            chat_in = st.text_input("Chat ID", value=current_chat, key="tg_chat")
            both = bool(token_in and chat_in)

            cb1, cb2 = st.columns(2)
            if cb1.button("Send test", disabled=not both, width="stretch"):
                try:
                    notifier = TelegramNotifier(token=token_in, chat_id=chat_in)
                    notifier.notify(
                        "INFO",
                        "Test from traderbot",
                        "If you see this in Telegram, alerts are wired correctly.",
                    )
                    st.success("Test message sent — check your Telegram.")
                except Exception as e:
                    st.error(f"Failed: {type(e).__name__}: {e}")
            if cb2.button("Save to .env", disabled=not both, type="primary", width="stretch"):
                env_config.update_env({"TELEGRAM_BOT_TOKEN": token_in, "TELEGRAM_CHAT_ID": chat_in})
                st.success("Saved. Stop + start the loop to pick up the new values.")

        st.divider()
        st.subheader("EVENT COUNTS")
        for k, v in sorted(event_counts(rows).items()):
            st.markdown(
                f'<div style="display:flex; justify-content:space-between; font-size:12px;">'
                f'<span style="color:#9ca3af;">{k}</span><span>{v}</span></div>',
                unsafe_allow_html=True,
            )
        st.divider()
        if st.button("Refresh now", width="stretch"):
            st.rerun()
        st.caption(f"Log: `{log_path}`  ·  Initial cash: ${initial_cash:,.0f}")


def main() -> None:
    log_path = Path(os.environ.get("TRADERBOT_LOG_PATH", str(DEFAULT_DB_PATH)))
    initial_cash = float(os.environ.get("TRADERBOT_INITIAL_CASH", "10000"))
    kill_switch_path = Path(
        os.environ.get("TRADERBOT_KILL_SWITCH_PATH", str(DEFAULT_KILL_SWITCH_PATH))
    )
    render(log_path, initial_cash, kill_switch_path)


main()
