"""Streamlit dashboard for the paper-trading bot.

Run: `uv run streamlit run ui/dashboard.py`. Reads the SQLite decision log at
`data/decision_log/traderbot.db` (override with env `TRADERBOT_LOG_PATH`). Auto-refreshes
every 10 seconds. The kill switch is a file at `data/state/KILL_SWITCH` — toggle from
the sidebar.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from memory.decision_log import DecisionLog
from risk.caps import DEFAULT_KILL_SWITCH_PATH, kill_switch_active
from ui.views import event_counts, fills_dataframe, summary, trades_dataframe

DEFAULT_DB_PATH = Path("data/decision_log/traderbot.db")


def _ts_to_str(ts_ms: int) -> str:
    return pd.to_datetime(ts_ms, unit="ms", utc=True).strftime("%Y-%m-%d %H:%M UTC")


def render(log_path: Path, initial_cash: float, kill_switch_path: Path) -> None:
    st.set_page_config(page_title="traderbot", layout="wide")
    st.title("traderbot — paper")

    if not log_path.exists():
        st.warning(f"No decision log at `{log_path}`. Start the live loop and refresh.")
        return

    log = DecisionLog(log_path)
    rows = log.all()
    log.close()

    with st.sidebar:
        st.subheader("Kill switch")
        active = kill_switch_active(kill_switch_path)
        if active:
            st.error("ACTIVE — bot is paused")
            if st.button("Disable kill switch"):
                if kill_switch_path.exists():
                    kill_switch_path.unlink()
                st.rerun()
        else:
            st.success("OFF — bot may trade")
            if st.button("Enable kill switch"):
                kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
                kill_switch_path.touch()
                st.rerun()
        st.caption(
            f"File: `{kill_switch_path}`. The env `KILL_SWITCH=true` also works "
            "and overrides the file."
        )
        st.divider()
        st.subheader("Auto-refresh")
        if st.button("Refresh now"):
            st.rerun()

    s = summary(rows, initial_cash=initial_cash)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Realized P&L", f"${s['realized_pnl']:,.2f}", f"{s['ending_return_pct'] * 100:+.2f} %"
    )
    c2.metric("Trades", s["trades"])
    c3.metric("Win rate", f"{s['win_rate'] * 100:.1f} %")
    c4.metric("Decision rows", s["rows_total"])

    st.subheader("Event counts")
    st.write(event_counts(rows))

    if s["blocks_by_reason"]:
        st.subheader("Risk blocks")
        st.write(s["blocks_by_reason"])

    st.subheader("Recent trades")
    trades = trades_dataframe(rows)
    if trades.empty:
        st.info("No completed trades yet.")
    else:
        trades_display = trades.copy()
        trades_display["entry"] = trades_display["entry_ts"].apply(_ts_to_str)
        trades_display["exit"] = trades_display["exit_ts"].apply(_ts_to_str)
        trades_display["return %"] = (trades_display["return_pct"] * 100).round(2)
        st.dataframe(
            trades_display[
                [
                    "entry",
                    "exit",
                    "qty",
                    "entry_price",
                    "exit_price",
                    "pnl",
                    "return %",
                    "exit_reason",
                ]
            ].tail(20),
            width="stretch",
        )

    st.subheader("Recent fills (last 30)")
    fills = fills_dataframe(rows)
    if fills.empty:
        st.info("No fills yet.")
    else:
        fills_display = fills.copy()
        fills_display["time"] = fills_display["timestamp_ms"].apply(_ts_to_str)
        st.dataframe(
            fills_display[["time", "side", "symbol", "quantity", "price", "rationale"]].tail(30),
            width="stretch",
        )


def main() -> None:
    log_path = Path(os.environ.get("TRADERBOT_LOG_PATH", str(DEFAULT_DB_PATH)))
    initial_cash = float(os.environ.get("TRADERBOT_INITIAL_CASH", "10000"))
    kill_switch_path = Path(
        os.environ.get("TRADERBOT_KILL_SWITCH_PATH", str(DEFAULT_KILL_SWITCH_PATH))
    )
    render(log_path, initial_cash, kill_switch_path)


main()
