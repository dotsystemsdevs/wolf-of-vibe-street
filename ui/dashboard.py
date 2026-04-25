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
REFRESH_INTERVAL_S = 10

GREEN = "#22c55e"
RED = "#ef4444"
GREY = "#6b7280"
GOLD = "#fbbf24"

CSS = """
<style>
:root {
  --green: #22c55e;
  --red: #ef4444;
  --grey: #6b7280;
  --gold: #fbbf24;
  --orange: #fb923c;
  --bg: #0a0e15;
  --card: #0f1623;
  --border: #1f2937;
  --text: #e5e7eb;
}

.stApp { background: var(--bg) !important; }

/* --- Brand title (Wolf Of Wall Street energy) --- */
.brand {
  font-family: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 22px; font-weight: 900; letter-spacing: 0.02em;
  color: var(--text);
}
.brand .accent { color: var(--gold); }
.brand .sub { color: var(--grey); font-size: 12px; font-weight: 500;
  letter-spacing: 0.18em; margin-left: 12px; text-transform: uppercase; }

/* --- KPI cards: gold top-border, mono numbers --- */
.kpi {
  background: var(--card);
  border: 1px solid var(--border);
  border-top: 2px solid var(--accent, var(--orange));
  border-radius: 4px;
  padding: 14px 18px;
  height: 100%;
}
.kpi .label {
  font-size: 10px; color: #9ca3af;
  text-transform: uppercase; letter-spacing: 0.14em;
  margin-bottom: 8px;
}
.kpi .value {
  font-family: "SF Mono", Menlo, monospace;
  font-size: 28px; font-weight: 700; line-height: 1.0;
  color: var(--text); letter-spacing: -0.01em;
}
.kpi .delta {
  font-size: 11px; color: #9ca3af;
  margin-top: 6px; letter-spacing: 0.04em;
}
.kpi.green   { --accent: var(--green); }
.kpi.green   .value { color: var(--green); }
.kpi.red     { --accent: var(--red); }
.kpi.red     .value { color: var(--red); }
.kpi.gold    { --accent: var(--gold); }
.kpi.gold    .value { color: var(--gold); }
.kpi.orange  { --accent: var(--orange); }
.kpi.white   { --accent: #e5e7eb; }

/* --- Panel chrome (orange section labels) --- */
.panel-title {
  font-size: 10px; color: var(--orange);
  text-transform: uppercase; letter-spacing: 0.16em;
  font-weight: 700;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border);
  display: flex; justify-content: space-between; align-items: center;
}
.panel-title .right { color: #6b7280; font-weight: 500; letter-spacing: 0.06em; }
.panel {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 4px;
  margin-top: 6px;
}
.panel .body { padding: 8px 4px; }

/* --- Action badges (BUY green / CLOSE red) --- */
.act {
  display: inline-block; padding: 3px 10px; border-radius: 3px;
  font-size: 10px; font-weight: 800; letter-spacing: 0.08em;
  font-family: "SF Mono", Menlo, monospace;
}
.act-buy   { background: rgba(34,197,94,0.18); color: var(--green); }
.act-close { background: rgba(239,68,68,0.18); color: var(--red); }
.act-stop  { background: rgba(239,68,68,0.10); color: #fca5a5; }
.act-tgt   { background: rgba(34,197,94,0.10); color: #86efac; }

/* --- Tables --- */
.t {
  width: 100%; border-collapse: collapse;
  font-family: "SF Mono", Menlo, monospace;
  font-size: 11px;
}
.t thead th {
  font-size: 9px; color: #6b7280;
  text-transform: uppercase; letter-spacing: 0.10em;
  text-align: left; padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  font-weight: 600;
}
.t tbody td {
  padding: 9px 10px; border-bottom: 1px solid #1a2330;
  color: var(--text);
}
.t tbody tr:hover { background: rgba(255,255,255,0.02); }
.t .num { text-align: right; font-variant-numeric: tabular-nums; }
.t .pos { color: var(--green); font-weight: 600; }
.t .neg { color: var(--red);   font-weight: 600; }
.t .muted { color: #6b7280; }

/* --- Live log --- */
.live-log {
  background: var(--bg);
  font-family: "SF Mono", Menlo, monospace;
  font-size: 11px; line-height: 1.65;
  padding: 8px 14px;
  max-height: 380px; overflow-y: auto;
  color: #d1d5db;
}
.live-log .ts   { color: #6b7280; }
.live-log .info { color: #93c5fd; }
.live-log .ok   { color: var(--green); }
.live-log .err  { color: var(--red); }
.live-log .warn { color: var(--gold); }
.live-log .sym  { color: var(--orange); }

/* --- Footer disclaimer --- */
.footer {
  margin-top: 18px; padding: 10px 14px;
  border-top: 1px solid var(--border);
  display: flex; justify-content: space-between;
  font-family: "SF Mono", Menlo, monospace;
  font-size: 10px; color: #6b7280;
  letter-spacing: 0.10em; text-transform: uppercase;
}

/* --- Status dot (header) --- */
.dot { display: inline-block; width: 8px; height: 8px;
  border-radius: 50%; margin-right: 6px; vertical-align: middle; }
.dot-green { background: var(--green); box-shadow: 0 0 6px var(--green); }
.dot-red   { background: var(--red);   box-shadow: 0 0 6px var(--red); }

/* Hide Streamlit chrome we don't want */
header[data-testid="stHeader"] { background: transparent; }
.stDeployButton { display: none; }
.muted { color: #6b7280; font-size: 11px; }
.section-title {
  font-size: 10px; color: var(--orange); text-transform: uppercase;
  letter-spacing: 0.14em; font-weight: 700;
  margin: 14px 0 6px 0; padding-bottom: 4px;
  border-bottom: 1px solid var(--border);
}
.kill-on  { color: var(--red);   font-weight: 700; }
.kill-off { color: var(--green); font-weight: 700; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.04em; }
.badge-stop { background: rgba(239,68,68,0.10); color: #fca5a5; }
.badge-tgt  { background: rgba(34,197,94,0.10); color: #86efac; }
.badge-exit { background: rgba(107,114,128,0.20); color: var(--grey); }
.log-line { font-family: "SF Mono", Menlo, monospace; font-size: 11px;
    padding: 2px 0; color: #d1d5db; line-height: 1.4; }
.log-time { color: #6b7280; }
.log-buy  { color: var(--green); }
.log-sell { color: var(--red); }
.log-block{ color: #fbbf24; }
.log-sig  { color: #60a5fa; }
</style>
"""


def _ts_to_str(ts_ms: int, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return pd.to_datetime(ts_ms, unit="ms", utc=True).strftime(fmt)


def _fmt_money(v: float, sign: bool = False) -> str:
    s = f"{v:+,.2f}" if sign else f"{v:,.2f}"
    return f"${s}"


def _equity_chart(eq_df: pd.DataFrame, initial_cash: float) -> go.Figure:
    fig = go.Figure()
    if eq_df.empty:
        fig.add_annotation(
            text="No fills yet — waiting for the first trade.",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font={"color": "#6b7280", "size": 14},
        )
    else:
        ts = pd.to_datetime(eq_df["timestamp_ms"], unit="ms", utc=True)
        fig.add_trace(
            go.Scatter(
                x=ts,
                y=eq_df["equity"],
                mode="lines",
                line={"color": GOLD, "width": 2, "shape": "hv"},
                fill="tozeroy",
                fillcolor="rgba(251,191,36,0.05)",
                name="Equity",
                hovertemplate="%{y:$,.2f}<extra></extra>",
            )
        )
        fig.add_hline(y=initial_cash, line={"color": "#374151", "width": 1, "dash": "dot"})

    fig.update_layout(
        height=260,
        margin={"l": 4, "r": 4, "t": 4, "b": 4},
        paper_bgcolor="#141a26",
        plot_bgcolor="#141a26",
        xaxis={
            "gridcolor": "#1f2937",
            "showspikes": True,
            "spikemode": "across",
            "spikecolor": "#374151",
            "spikethickness": 1,
        },
        yaxis={"gridcolor": "#1f2937", "tickprefix": "$", "tickformat": ",.0f"},
        showlegend=False,
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

    # --- Header: brand left, clock + status dot right (matches reference) ---
    now_utc_str = pd.Timestamp.utcnow().strftime("%H:%M:%S UTC")
    loop_alive = loop_control.status().running
    kill_on = kill_switch_active(kill_switch_path)
    dot_cls = "dot-green" if loop_alive and not kill_on else "dot-red"
    dot_label = "LIVE" if loop_alive and not kill_on else ("PAUSED" if kill_on else "OFFLINE")
    st.markdown(
        f'<div style="display:flex; justify-content:space-between; align-items:center; '
        f'padding-bottom:14px; border-bottom:1px solid #1f2937; margin-bottom:14px;">'
        f'<div class="brand">🐺 <span class="accent">WOLF</span> OF '
        f'<span class="accent">VIBE</span> STREET'
        f'<span class="sub">DASHBOARD · PAPER</span></div>'
        f"<div style=\"font-family:'SF Mono',Menlo,monospace; font-size:12px; "
        f'color:#9ca3af; letter-spacing:0.06em;">'
        f"{now_utc_str} &nbsp;·&nbsp; "
        f'<span class="dot {dot_cls}"></span><span style="color:#e5e7eb;">{dot_label}</span>'
        f"</div></div>",
        unsafe_allow_html=True,
    )

    if not rows:
        st.warning(
            f"No decision log yet at `{log_path}`. Start the live loop "
            f"(`uv run python -m workers.live_loop`) — page auto-refreshes."
        )

    tab_overview, tab_compare = st.tabs(["📊 Overview", "🔬 Backtest compare"])

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
        banner_color = {"ok": GREEN, "warn": GOLD, "error": RED}[worst]
        banner_label = {"ok": "HEALTHY", "warn": "ATTENTION", "error": "ISSUES"}[worst]
        st.markdown(
            f'<div style="background:#141a26; border:1px solid {banner_color}; '
            f"border-left:4px solid {banner_color}; border-radius:6px; "
            f'padding:10px 14px; margin-bottom:10px;">'
            f'<div style="display:flex; justify-content:space-between; align-items:center;">'
            f'<span style="font-size:12px; color:#9ca3af; text-transform:uppercase; '
            f'letter-spacing:0.1em;">Soak status</span>'
            f'<span style="color:{banner_color}; font-weight:700; font-size:13px; '
            f'letter-spacing:0.05em;">{banner_label}</span></div>'
            f'<div style="margin-top:6px; display:grid; '
            f"grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:6px; "
            f'font-size:11px;">'
            + "".join(
                '<div><span style="color:'
                + {"ok": GREEN, "warn": GOLD, "error": RED}[c["status"]]
                + '">●</span> '
                f'<span style="color:#e5e7eb;">{c["name"]}</span> '
                f'<span style="color:#9ca3af;">— {c["message"]}</span></div>'
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
        equity_color = (
            "white" if abs(total_return) < 0.001 else ("green" if total_return > 0 else "red")
        )
        cash_color = "red" if current_cash < 0 else "white"
        day_color = "green" if today_pnl >= 0 else "red"
        vs_start_color = "gold"

        def _kpi(color: str, label: str, value: str, delta: str = "") -> str:
            delta_html = f'<div class="delta">{delta}</div>' if delta else ""
            return (
                f'<div class="kpi {color}">'
                f'<div class="label">{label}</div>'
                f'<div class="value">{value}</div>'
                f"{delta_html}</div>"
            )

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.markdown(
            _kpi(
                equity_color,
                "Equity",
                f"${current_equity:,.2f}",
                f"{total_return * 100:+.2f}% vs start",
            ),
            unsafe_allow_html=True,
        )
        k2.markdown(
            _kpi(cash_color, "Cash", f"${current_cash:,.2f}"),
            unsafe_allow_html=True,
        )
        k3.markdown(
            _kpi(day_color, "Day P&L", f"${today_pnl:+,.2f}" if today_pnl else "$0.00", ""),
            unsafe_allow_html=True,
        )
        k4.markdown(
            _kpi(
                vs_start_color,
                "Vs. start",
                f"${current_equity - initial_cash:+,.2f}",
                f"{total_return * 100:+.2f}%",
            ),
            unsafe_allow_html=True,
        )
        k5.markdown(
            _kpi("white", "Positions", str(len(positions)), f"max {max_pos}"),
            unsafe_allow_html=True,
        )

        st.write("")  # spacer

        # --- Row 1: Equity curve (left, 2x) + Open positions (right, 1x) ---
        left, right = st.columns([2, 1])
        with left:
            total_pct = total_return * 100
            st.markdown(
                f'<div class="panel"><div class="panel-title">Equity curve'
                f'<span class="right">{total_pct:+.2f}% total</span></div>',
                unsafe_allow_html=True,
            )
            st.plotly_chart(
                _equity_chart(eq_df, initial_cash),
                config={"displayModeBar": False},
                width="stretch",
            )
            st.markdown("</div>", unsafe_allow_html=True)

        with right:
            st.markdown(
                f'<div class="panel"><div class="panel-title">Open positions'
                f'<span class="right">{len(positions)} / {max_pos}</span></div>'
                f'<div class="body">{_positions_html(positions)}</div></div>',
                unsafe_allow_html=True,
            )

        # --- Row 2: Trade history (left, 2x) + Live log (right, 1x) ---
        left2, right2 = st.columns([2, 1])
        with left2:
            st.markdown(
                f'<div class="panel"><div class="panel-title">Trade history'
                f'<span class="right">last 25</span></div>'
                f'<div class="body">{_trades_table_html(trades_dataframe(rows))}</div></div>',
                unsafe_allow_html=True,
            )

        with right2:
            log_html = _live_log_html(rows, n=80)
            st.markdown(
                f'<div class="panel"><div class="panel-title">Live log'
                f'<span class="right">last 80 · auto-scroll</span></div>'
                f"{log_html}</div>",
                unsafe_allow_html=True,
            )
            if s["blocks_by_reason"]:
                st.markdown('<div class="section-title">Risk blocks</div>', unsafe_allow_html=True)
                for reason, count in sorted(s["blocks_by_reason"].items(), key=lambda kv: -kv[1]):
                    st.markdown(
                        f'<div style="display:flex; justify-content:space-between; '
                        f'padding:4px 0; font-size:12px;">'
                        f'<span style="color:#fbbf24;">{reason}</span>'
                        f'<span style="color:#9ca3af;">{count}</span></div>',
                        unsafe_allow_html=True,
                    )

        # --- Loop output (full width below) ---
        st.markdown(
            '<div class="panel"><div class="panel-title">Loop output'
            '<span class="right">last 30 lines</span></div>',
            unsafe_allow_html=True,
        )
        loop_log_text = (
            loop_control.tail_log(lines=30) or "(no loop log yet — start the loop from the sidebar)"
        )
        st.code(loop_log_text, language="bash")
        st.markdown("</div>", unsafe_allow_html=True)

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
        st.subheader("Live loop")
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

        with st.expander("⚠ Reset for fresh soak", expanded=False):
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
        st.subheader("Kill switch")
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
        with st.expander("📱 Telegram alerts", expanded=False):
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
        st.subheader("Event counts")
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
