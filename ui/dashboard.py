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

from memory.decision_log import DecisionLog  # noqa: E402
from risk.caps import DEFAULT_KILL_SWITCH_PATH, kill_switch_active  # noqa: E402
from ui.views import (  # noqa: E402
    equity_curve,
    event_counts,
    open_positions,
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

    if not log_path.exists():
        st.warning(f"No decision log at `{log_path}`. Run the live loop and refresh.")
        return

    log = DecisionLog(log_path)
    rows = log.all()
    log.close()

    s = summary(rows, initial_cash=initial_cash)
    eq_df = equity_curve(rows, initial_cash=initial_cash)
    positions = open_positions(rows)

    current_equity = float(eq_df.iloc[-1]["equity"]) if not eq_df.empty else initial_cash
    current_cash = float(eq_df.iloc[-1]["cash"]) if not eq_df.empty else initial_cash
    total_return = (current_equity - initial_cash) / initial_cash if initial_cash > 0 else 0
    realized_pnl = s["realized_pnl"]

    st.markdown(
        f'<div style="display:flex; justify-content:space-between; align-items:center; '
        f'margin-bottom:8px;">'
        f'<div><span style="font-size:18px; font-weight:700; letter-spacing:0.06em; '
        f'color:#fbbf24;">TRADING / BOT</span> '
        f'<span class="muted" style="margin-left:12px;">DASHBOARD · paper</span></div>'
        f'<div style="font-size:11px; color:#9ca3af;">'
        f"kill switch: "
        f'<span class="{"kill-on" if kill_switch_active(kill_switch_path) else "kill-off"}">'
        f"{'ACTIVE' if kill_switch_active(kill_switch_path) else 'OFF'}</span>"
        f" · auto-refresh {REFRESH_INTERVAL_S}s</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Equity", _fmt_money(current_equity), f"{total_return * 100:+.2f} %")
    c2.metric("Cash", _fmt_money(current_cash))
    c3.metric("Realized P&L", _fmt_money(realized_pnl, sign=True), f"{s['win_rate'] * 100:.1f}% WR")
    c4.metric("Trades", s["trades"], f"{s['wins']}W / {s['losses']}L")
    c5.metric("Open positions", len(positions))

    left, right = st.columns([2, 1])

    with left:
        st.markdown('<div class="section-title">Equity curve</div>', unsafe_allow_html=True)
        st.plotly_chart(
            _equity_chart(eq_df, initial_cash), config={"displayModeBar": False}, width="stretch"
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

    with st.sidebar:
        st.subheader("Controls")
        if kill_switch_active(kill_switch_path):
            st.error("KILL SWITCH ACTIVE")
            if st.button("Disable kill switch", type="primary"):
                if kill_switch_path.exists():
                    kill_switch_path.unlink()
                st.rerun()
        else:
            st.success("Kill switch OFF")
            if st.button("Enable kill switch", type="secondary"):
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
        if st.button("Refresh now"):
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
