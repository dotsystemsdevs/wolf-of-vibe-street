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
from tools import loop_control  # noqa: E402
from ui.views import (  # noqa: E402
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
:root { --green: #22c55e; --red: #ef4444; --grey: #6b7280; --gold: #fbbf24; }

div[data-testid="stMetric"] {
    background: #141a26;
    border: 1px solid #1f2937;
    border-radius: 8px;
    padding: 14px 16px;
    box-shadow: 0 1px 0 rgba(255,255,255,0.04) inset;
}
div[data-testid="stMetricLabel"] { color: #9ca3af; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.08em; }
div[data-testid="stMetricValue"] { font-size: 26px; font-weight: 600; color: #e5e7eb; }
div[data-testid="stMetricDelta"] { font-size: 13px; }

.section-title {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: #9ca3af;
    margin: 16px 0 6px 0;
    padding-bottom: 4px;
    border-bottom: 1px solid #1f2937;
}

.badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.04em; }
.badge-buy  { background: rgba(34,197,94,0.15);  color: var(--green); }
.badge-sell { background: rgba(239,68,68,0.15);  color: var(--red); }
.badge-stop { background: rgba(239,68,68,0.10);  color: #fca5a5; }
.badge-tgt  { background: rgba(34,197,94,0.10);  color: #86efac; }
.badge-exit { background: rgba(107,114,128,0.20); color: var(--grey); }

.kill-on  { color: var(--red);   font-weight: 700; }
.kill-off { color: var(--green); font-weight: 700; }
.muted    { color: #6b7280; font-size: 11px; }

.log-line { font-family: "SF Mono", Menlo, monospace; font-size: 11px;
    padding: 2px 0; color: #d1d5db; line-height: 1.4; }
.log-time { color: #6b7280; }
.log-buy  { color: var(--green); }
.log-sell { color: var(--red); }
.log-block{ color: #fbbf24; }
.log-sig  { color: #60a5fa; }

.stDataFrame { font-size: 12px; }
header[data-testid="stHeader"] { background: transparent; }
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


def _trades_table_html(trades: pd.DataFrame) -> str:
    if trades.empty:
        return '<div class="muted">No completed trades yet.</div>'
    rows_html: list[str] = []
    for _, t in trades.tail(15).iloc[::-1].iterrows():
        pnl = float(t["pnl"])
        pnl_color = GREEN if pnl > 0 else (RED if pnl < 0 else GREY)
        ret = float(t["return_pct"]) * 100
        reason = t["exit_reason"] or ""
        if "stop" in reason:
            badge = _badge("STOP", "stop")
        elif "target" in reason:
            badge = _badge("TGT", "tgt")
        else:
            badge = _badge("EXIT", "exit")
        rows_html.append(
            f'<tr style="border-bottom:1px solid #1f2937;">'
            f'<td style="padding:6px 8px; color:#9ca3af;">{_ts_to_str(int(t["entry_ts"]))}</td>'
            f'<td style="padding:6px 8px; color:#9ca3af;">{_ts_to_str(int(t["exit_ts"]))}</td>'
            f'<td style="padding:6px 8px; text-align:right; font-variant-numeric:tabular-nums;">'
            f"{float(t['entry_price']):,.2f}</td>"
            f'<td style="padding:6px 8px; text-align:right; font-variant-numeric:tabular-nums;">'
            f"{float(t['exit_price']):,.2f}</td>"
            f'<td style="padding:6px 8px; text-align:right; font-variant-numeric:tabular-nums; '
            f'color:{pnl_color}; font-weight:600;">{pnl:+,.2f}</td>'
            f'<td style="padding:6px 8px; text-align:right; font-variant-numeric:tabular-nums; '
            f'color:{pnl_color};">{ret:+.2f}%</td>'
            f'<td style="padding:6px 8px;">{badge}</td>'
            f"</tr>"
        )
    return (
        '<table style="width:100%; border-collapse:collapse; font-size:12px;">'
        '<thead><tr style="color:#9ca3af; font-size:10px; text-transform:uppercase; '
        'letter-spacing:0.08em; border-bottom:1px solid #374151;">'
        '<th style="padding:6px 8px; text-align:left;">Entry</th>'
        '<th style="padding:6px 8px; text-align:left;">Exit</th>'
        '<th style="padding:6px 8px; text-align:right;">Entry $</th>'
        '<th style="padding:6px 8px; text-align:right;">Exit $</th>'
        '<th style="padding:6px 8px; text-align:right;">P&amp;L</th>'
        '<th style="padding:6px 8px; text-align:right;">Return</th>'
        '<th style="padding:6px 8px;">Reason</th>'
        "</tr></thead><tbody>" + "".join(rows_html) + "</tbody></table>"
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
        return '<div class="muted">No open positions.</div>'
    rows_html: list[str] = []
    for p in positions:
        upnl = float(p["unrealized_pnl"])
        color = GREEN if upnl > 0 else (RED if upnl < 0 else GREY)
        rows_html.append(
            f'<tr style="border-bottom:1px solid #1f2937;">'
            f'<td style="padding:8px;"><strong>{p["symbol"]}</strong></td>'
            f'<td style="padding:8px; text-align:right; font-variant-numeric:tabular-nums;">'
            f"{p['qty']:.6f}</td>"
            f'<td style="padding:8px; text-align:right; font-variant-numeric:tabular-nums;">'
            f"{p['avg_entry']:,.2f}</td>"
            f'<td style="padding:8px; text-align:right; font-variant-numeric:tabular-nums;">'
            f"{p['last_price']:,.2f}</td>"
            f'<td style="padding:8px; text-align:right; font-variant-numeric:tabular-nums; '
            f'color:{color}; font-weight:600;">{upnl:+,.2f}</td>'
            f"</tr>"
        )
    return (
        '<table style="width:100%; border-collapse:collapse; font-size:12px;">'
        '<thead><tr style="color:#9ca3af; font-size:10px; text-transform:uppercase; '
        'letter-spacing:0.08em; border-bottom:1px solid #374151;">'
        '<th style="padding:6px 8px; text-align:left;">Symbol</th>'
        '<th style="padding:6px 8px; text-align:right;">Qty</th>'
        '<th style="padding:6px 8px; text-align:right;">Avg Entry</th>'
        '<th style="padding:6px 8px; text-align:right;">Mark</th>'
        '<th style="padding:6px 8px; text-align:right;">uPnL</th>'
        "</tr></thead><tbody>" + "".join(rows_html) + "</tbody></table>"
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
    fills = [r for r in rows if r["event_type"] == "order_filled"]
    last_bar_ts = max((int(r["timestamp_ms"]) for r in fills), default=None)
    active_symbols = sorted({r["symbol"] for r in rows}) if rows else []

    current_equity = float(eq_df.iloc[-1]["equity"]) if not eq_df.empty else initial_cash
    current_cash = float(eq_df.iloc[-1]["cash"]) if not eq_df.empty else initial_cash
    total_return = (current_equity - initial_cash) / initial_cash if initial_cash > 0 else 0
    realized_pnl = s["realized_pnl"]

    last_bar_str = _ts_to_str(last_bar_ts, "%Y-%m-%d %H:%M UTC") if last_bar_ts else "—"
    symbols_str = ", ".join(active_symbols) if active_symbols else "—"
    st.markdown(
        f'<div style="display:flex; justify-content:space-between; align-items:center; '
        f'margin-bottom:8px;">'
        f'<div><span style="font-size:18px; font-weight:700; letter-spacing:0.06em; '
        f'color:#fbbf24;">TRADING / BOT</span> '
        f'<span class="muted" style="margin-left:12px;">DASHBOARD · paper</span></div>'
        f'<div style="font-size:11px; color:#9ca3af; text-align:right;">'
        f'<div>last fill: <span style="color:#e5e7eb;">{last_bar_str}</span> '
        f'· symbols: <span style="color:#e5e7eb;">{symbols_str}</span></div>'
        f"<div>kill switch: "
        f'<span class="{"kill-on" if kill_switch_active(kill_switch_path) else "kill-off"}">'
        f"{'ACTIVE' if kill_switch_active(kill_switch_path) else 'OFF'}</span>"
        f" · auto-refresh {REFRESH_INTERVAL_S}s</div></div>"
        f"</div>",
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

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Equity", _fmt_money(current_equity), f"{total_return * 100:+.2f} %")
        c2.metric("Cash", _fmt_money(current_cash))
        c3.metric(
            "Realized P&L",
            _fmt_money(realized_pnl, sign=True),
            f"{s['win_rate'] * 100:.1f}% WR",
        )
        c4.metric("Trades", s["trades"], f"{s['wins']}W / {s['losses']}L")
        c5.metric("Open positions", len(positions))

        left, right = st.columns([2, 1])
        with left:
            st.markdown('<div class="section-title">Equity curve</div>', unsafe_allow_html=True)
            st.plotly_chart(
                _equity_chart(eq_df, initial_cash),
                config={"displayModeBar": False},
                width="stretch",
            )
            st.markdown('<div class="section-title">Trade history</div>', unsafe_allow_html=True)
            st.markdown(_trades_table_html(trades_dataframe(rows)), unsafe_allow_html=True)

        with right:
            st.markdown('<div class="section-title">Open positions</div>', unsafe_allow_html=True)
            st.markdown(_positions_html(positions), unsafe_allow_html=True)
            st.markdown('<div class="section-title">Live log</div>', unsafe_allow_html=True)
            st.markdown(_live_log_html(rows), unsafe_allow_html=True)
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

        st.markdown(
            '<div class="section-title">Loop output (last 30 lines)</div>',
            unsafe_allow_html=True,
        )
        loop_log_text = (
            loop_control.tail_log(lines=30) or "(no loop log yet — start the loop from the sidebar)"
        )
        st.code(loop_log_text, language="bash")

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
