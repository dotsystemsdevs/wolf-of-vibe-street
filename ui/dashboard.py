"""Streamlit dashboard for the paper-trading bot.

Run: `uv run streamlit run ui/dashboard.py`. Dark theme via `.streamlit/config.toml`.
Reads SQLite decision log at `data/decision_log/traderbot.db` (override with env
`TRADERBOT_LOG_PATH`). Auto-refreshes periodically (default 30s, see `REFRESH_INTERVAL_S`).
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
import streamlit.components.v1 as components  # noqa: E402

from backtest.compare import (  # noqa: E402
    DEFAULT_STRATEGY_ID,
    DEFAULT_SYMBOLS,
    STRATEGIES,
    make_figure,
    rank_by_expectancy,
    run_comparison,
    strategy_by_label,
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
# Bump when UI changes — if you do not see this in the header, you are not running this file.
DASHBOARD_BUILD = "2026-04-26o"

GREEN = "#22c55e"
RED = "#ef4444"
GREY = "#6b7280"
GOLD = "#fbbf24"

FONT_LINK = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Oswald:wght@500;600;700&family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">
"""

CSS = """
<style>
/* Wolf-of-Vibe-Street execution desk — black floor, gold edge, tape typography.
 * Color rule: GOLD is the only chrome accent (status hints, edges, separators).
 * GREEN/RED are reserved for *real events* — P&L, win/loss, buy/close, RUN/OFF.
 * If a value is not a P&L outcome or live state, it must NOT be green or red. */
:root {
  --bg:        #070707;
  --card:      #0e0e0e;
  --border:    #1a1a1a;
  --border-2:  #2a2a2a;
  --text:      #f0f0f0;
  --text-2:    #a8a8a8;
  --text-3:    #6b6b6b;
  --accent:    #fbbf24;             /* gold — used for chrome only */
  --accent-dim: rgba(251, 191, 36, 0.32);
  --green:     #22c55e;             /* P&L positive, RUN, BUY */
  --red:       #ef4444;             /* P&L negative, OFF, CLOSE */
}

.stApp {
  background-color: var(--bg) !important;
  background-image:
    linear-gradient(165deg, rgba(251, 191, 36, 0.025) 0%, transparent 42%),
    repeating-linear-gradient(
      0deg, transparent, transparent 48px,
      rgba(255,255,255,0.012) 48px, rgba(255,255,255,0.012) 49px
    ) !important;
}
header[data-testid="stHeader"] { background: transparent; }
.stDeployButton, footer { display: none; }
.block-container { padding-top: 1.25rem; padding-bottom: 1rem; max-width: 100%; }

/* --- Header: main title + tape --- */
.wolf-header {
  padding: 4px 0 18px 0;
  margin-bottom: 8px;
  border-bottom: 2px solid transparent;
  border-image: linear-gradient(90deg, var(--green), var(--accent), var(--red)) 1;
}
.wolf-main {
  font-family: "Bebas Neue", Impact, sans-serif;
  font-size: clamp(28px, 4.5vw, 40px);
  line-height: 0.95;
  letter-spacing: 0.14em;
  color: #fafafa;
  text-transform: uppercase;
  text-shadow: 0 0 42px rgba(234, 88, 12, 0.2);
}
.wolf-tagline {
  font-family: "Oswald", sans-serif;
  font-weight: 500;
  font-size: 11px;
  letter-spacing: 0.28em;
  text-transform: uppercase;
  color: var(--text-3);
  margin-top: 6px;
}
.wolf-dash-label {
  font-family: "Oswald", sans-serif;
  font-weight: 600;
  font-size: 11px;
  letter-spacing: 0.22em;
  color: var(--accent);
  margin-right: 8px;
}
.wolf-build {
  font-family: "JetBrains Mono", monospace;
  font-size: 10px;
  color: var(--text-3);
  letter-spacing: 0.08em;
  margin-left: 10px;
}

/* --- Mode tag (PAPER / LIVE) --- */
.mode {
  display: inline-block;
  padding: 3px 10px;
  font-family: "JetBrains Mono", monospace;
  font-size: 10px; font-weight: 700; letter-spacing: 0.12em;
  border: 1px solid;
  vertical-align: 2px;
}
.mode.paper {
  color: var(--text-2);
  border-color: var(--border-2);
  background: rgba(255,255,255,0.03);
}
.mode.live  { color: #fff; background: var(--red); border-color: var(--red); }

/* --- Status strip --- */
.status {
  font-family: "JetBrains Mono", monospace;
  font-size: 11px; letter-spacing: 0.06em;
  color: var(--text-2);
}
.status .v { color: var(--text); font-weight: 600; }
.status .v.run  { color: var(--green); }
.status .v.idle { color: var(--accent); }
.status .v.off  { color: var(--red); }

/* --- KPI cards — tape edge, mono numbers --- */
.kpi {
  background: linear-gradient(180deg, #121212 0%, var(--card) 100%);
  border: 1px solid var(--border);
  border-top: 2px solid var(--accent);
  padding: 12px 14px;
  height: 100%;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
}
.kpi .label {
  font-family: "Oswald", sans-serif;
  font-size: 9px; color: var(--text-3);
  text-transform: uppercase; letter-spacing: 0.2em;
  margin-bottom: 10px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.kpi .value {
  font-family: "JetBrains Mono", monospace;
  font-size: clamp(15px, 1.8vw, 22px);
  font-weight: 700; line-height: 1.0;
  color: var(--text); letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.kpi .delta {
  font-family: "JetBrains Mono", monospace;
  font-size: 11px; color: var(--text-3);
  margin-top: 6px; letter-spacing: 0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.kpi.pos .value { color: var(--green); }
.kpi.neg .value { color: var(--red); }
.kpi.pos .delta { color: var(--green); }
.kpi.neg .delta { color: var(--red); }

.kpi-mini {
  background: linear-gradient(180deg, #101010 0%, #0c0c0c 100%);
  border: 1px solid var(--border);
  border-left: 2px solid var(--accent-dim);
  padding: 8px 12px;
}
.kpi-mini .lbl {
  font-family: "Oswald", sans-serif;
  font-size: 9px; color: var(--text-3);
  text-transform: uppercase; letter-spacing: 0.18em;
  margin-bottom: 4px;
}
.kpi-mini .val {
  font-family: "JetBrains Mono", monospace;
  font-size: 16px; font-weight: 700;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

/* --- Section headers --- */
.sect {
  display: flex; justify-content: space-between; align-items: baseline;
  margin: 20px 0 8px 0; padding: 0 0 6px 0;
  border-bottom: 1px solid var(--border);
}
.sect .t {
  font-family: "Oswald", sans-serif;
  font-size: 11px; color: var(--text-2);
  text-transform: uppercase; letter-spacing: 0.22em;
  font-weight: 600;
}
.sect .r {
  font-family: "JetBrains Mono", monospace;
  font-size: 10px; color: var(--text-3); letter-spacing: 0.04em;
}

/* --- Action tags --- */
.act {
  display: inline-block; padding: 1px 6px;
  font-family: "JetBrains Mono", monospace;
  font-size: 10px; font-weight: 700; letter-spacing: 0.08em;
  border: 1px solid;
}
.act-buy   { color: var(--green); border-color: rgba(34,197,94,0.45); }
.act-close { color: var(--red);   border-color: rgba(239,68,68,0.45); }

/* --- Tables --- */
.t {
  width: 100%; border-collapse: collapse;
  font-family: "JetBrains Mono", monospace;
  font-size: 11px;
}
.t thead th {
  font-family: "Oswald", sans-serif;
  font-size: 9px; color: var(--text-3);
  text-transform: uppercase; letter-spacing: 0.16em;
  text-align: left; padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  font-weight: 600;
}
.t tbody td {
  padding: 7px 12px; border-bottom: 1px solid var(--border);
  color: var(--text);
}
.t tbody tr:hover { background: #141414; }
.t .num { text-align: right; font-variant-numeric: tabular-nums; }
.t .pos { color: var(--green); }
.t .neg { color: var(--red); }
.t .muted { color: var(--text-3); }

/* --- Activity feed (log) --- */
.feed {
  background: #080808;
  border: 1px solid var(--border);
  font-family: "JetBrains Mono", monospace;
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
.feed .log-line { padding: 2px 0; line-height: 1.5; }
.feed .log-time { color: var(--text-3); margin-right: 8px; }
.feed .log-buy { color: var(--green); }
.feed .log-sell { color: var(--red); }
.feed .log-block { color: var(--accent); }
.feed .log-sig { color: var(--text-2); }

/* --- Footer --- */
.footer {
  margin-top: 32px; padding: 14px 12px;
  border-top: 1px solid var(--border);
  display: flex; justify-content: space-between; align-items: center;
  font-family: "Oswald", sans-serif;
  font-size: 10px; color: var(--text-3);
  letter-spacing: 0.14em; text-transform: uppercase;
  clear: both;
}
.footer .mono {
  font-family: "JetBrains Mono", monospace;
  letter-spacing: 0.06em;
  color: var(--text-2);
}

/* Streamlit overrides */
.muted { color: var(--text-3); font-size: 11px; }
.section-title {
  font-family: "Oswald", sans-serif;
  font-size: 10px; color: var(--text-2); text-transform: uppercase;
  letter-spacing: 0.2em; font-weight: 600;
  margin: 14px 0 6px 0; padding-bottom: 4px;
  border-bottom: 1px solid var(--border);
}

/* --- Global Streamlit widget styling (applies to main + sidebar) --- */
.stButton > button {
  font-family: "Oswald", sans-serif !important;
  font-size: 11px !important;
  letter-spacing: 0.16em !important;
  text-transform: uppercase !important;
  font-weight: 600 !important;
  border-radius: 0 !important;
  border: 1px solid var(--border-2) !important;
  background: #0a0a0a !important;
  color: var(--text) !important;
  transition: border-color 0.12s, color 0.12s;
}
.stButton > button:hover {
  border-color: var(--accent) !important;
  color: var(--accent) !important;
}
.stButton > button[kind="primary"] {
  background: var(--accent) !important;
  color: #0a0a0a !important;
  border-color: var(--accent) !important;
}
.stButton > button[kind="primary"]:hover {
  background: #fcd34d !important;
  border-color: #fcd34d !important;
  color: #0a0a0a !important;
}
.stButton > button:disabled {
  opacity: 0.4 !important;
  border-color: var(--border) !important;
  color: var(--text-3) !important;
}
input, textarea, [data-baseweb="input"] input, [data-baseweb="select"] > div {
  background: #0a0a0a !important;
  border-radius: 0 !important;
  color: var(--text) !important;
  font-family: "JetBrains Mono", monospace !important;
}
input:focus, textarea:focus {
  border-color: var(--accent) !important;
  box-shadow: none !important;
}
[data-testid="stAlert"], [data-baseweb="notification"] {
  border-radius: 0 !important;
  background: #0a0a0a !important;
  border: 1px solid var(--border) !important;
  border-left-width: 2px !important;
  padding: 8px 12px !important;
  font-family: "JetBrains Mono", monospace !important;
  font-size: 11px !important;
  box-shadow: none !important;
}

/* Streamlit tabs — neutralize colors */
button[data-baseweb="tab"] {
  font-family: "Oswald", sans-serif !important;
  font-size: 11px !important; letter-spacing: 0.14em !important;
  text-transform: uppercase !important; font-weight: 600 !important;
  color: var(--text-3) !important;
}
button[data-baseweb="tab"][aria-selected="true"] { color: var(--text) !important; }
div[data-baseweb="tab-highlight"] { background: var(--accent) !important; }

/* --- Sidebar — match the desk: black floor, gold edges, Oswald headers --- */
[data-testid="stSidebar"] {
  background: #050505 !important;
  border-right: 1px solid var(--border);
}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
  padding-top: 1rem;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] .stSubheader {
  font-family: "Oswald", sans-serif !important;
  font-size: 12px !important; font-weight: 600 !important;
  letter-spacing: 0.22em !important; text-transform: uppercase !important;
  color: var(--accent) !important;
  margin: 14px 0 8px 0 !important;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border);
}
[data-testid="stSidebar"] hr,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] hr {
  border-color: var(--border) !important;
  margin: 12px 0 !important;
}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
[data-testid="stSidebar"] .stCaption,
[data-testid="stSidebar"] small {
  font-family: "JetBrains Mono", monospace !important;
  font-size: 10px !important;
  color: var(--text-3) !important;
  letter-spacing: 0.04em;
}
/* Sidebar buttons — flat, gold border on primary, mono text. */
[data-testid="stSidebar"] .stButton > button {
  font-family: "Oswald", sans-serif !important;
  font-size: 11px !important;
  letter-spacing: 0.16em !important;
  text-transform: uppercase !important;
  font-weight: 600 !important;
  border-radius: 0 !important;
  border: 1px solid var(--border-2) !important;
  background: #0a0a0a !important;
  color: var(--text) !important;
  transition: border-color 0.12s, color 0.12s;
}
[data-testid="stSidebar"] .stButton > button:hover {
  border-color: var(--accent) !important;
  color: var(--accent) !important;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"],
[data-testid="stSidebar"] .stButton > button[data-baseweb="button"][kind="primary"] {
  background: var(--accent) !important;
  color: #0a0a0a !important;
  border-color: var(--accent) !important;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {
  background: #fcd34d !important;
  border-color: #fcd34d !important;
}
[data-testid="stSidebar"] .stButton > button:disabled {
  opacity: 0.4 !important;
  border-color: var(--border) !important;
  color: var(--text-3) !important;
}
/* Sidebar status callouts (st.success/warning/error) — strip Streamlit's boxes,
 * keep only the meaning: green/yellow/red text on black with a thin left rule. */
[data-testid="stSidebar"] [data-testid="stAlert"],
[data-testid="stSidebar"] [data-baseweb="notification"] {
  border-radius: 0 !important;
  background: #0a0a0a !important;
  border: 1px solid var(--border) !important;
  border-left-width: 2px !important;
  padding: 8px 12px !important;
  font-family: "JetBrains Mono", monospace !important;
  font-size: 11px !important;
  box-shadow: none !important;
}
[data-testid="stSidebar"] [data-testid="stAlertContentSuccess"],
[data-testid="stSidebar"] [data-baseweb="notification"][kind="positive"] {
  border-left-color: var(--green) !important;
  color: var(--green) !important;
}
[data-testid="stSidebar"] [data-testid="stAlertContentWarning"],
[data-testid="stSidebar"] [data-baseweb="notification"][kind="warning"] {
  border-left-color: var(--accent) !important;
  color: var(--accent) !important;
}
[data-testid="stSidebar"] [data-testid="stAlertContentError"],
[data-testid="stSidebar"] [data-baseweb="notification"][kind="negative"] {
  border-left-color: var(--red) !important;
  color: var(--red) !important;
}
/* Sidebar expander — match section header pattern. */
[data-testid="stSidebar"] [data-testid="stExpander"] {
  background: transparent !important;
  border: 1px solid var(--border) !important;
  border-radius: 0 !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary {
  font-family: "Oswald", sans-serif !important;
  font-size: 11px !important;
  letter-spacing: 0.16em !important;
  text-transform: uppercase !important;
  color: var(--text-2) !important;
  font-weight: 600 !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover {
  color: var(--accent) !important;
}
/* Sidebar text inputs — black, gold focus ring. */
[data-testid="stSidebar"] input {
  background: #0a0a0a !important;
  border: 1px solid var(--border-2) !important;
  border-radius: 0 !important;
  color: var(--text) !important;
  font-family: "JetBrains Mono", monospace !important;
}
[data-testid="stSidebar"] input:focus {
  border-color: var(--accent) !important;
  box-shadow: none !important;
}
/* Sidebar checkbox label */
[data-testid="stSidebar"] [data-testid="stCheckbox"] label {
  font-family: "JetBrains Mono", monospace !important;
  font-size: 11px !important;
  color: var(--text-2) !important;
}
</style>
"""


@st.cache_data(ttl=86_400, show_spinner=False)
def _cached_symbol_ranking(
    symbols_key: tuple[str, ...],
    days: int,
    timeframe: str,
    strategy_id: str,
) -> list[dict]:
    """24-hour cached symbol-expectancy ranking, keyed on strategy as well.

    Returns plain dicts so the Streamlit cache can serialize them. Cache
    invalidates on any (symbols, days, timeframe, strategy) change, or via
    the panel's "Refresh" button (clears st.cache_data).
    """
    entry = STRATEGIES.get(strategy_id, STRATEGIES[DEFAULT_STRATEGY_ID])
    results = run_comparison(
        list(symbols_key), days=days, timeframe=timeframe, strategy_fn=entry.fn
    )
    ranked = rank_by_expectancy(results)
    return [
        {
            "symbol": r.symbol,
            "bars": int(r.bars),
            "buy_hold_return_pct": float(r.buy_hold_return_pct),
            "metrics": {k: float(v) for k, v in r.result.metrics.items()},
        }
        for r in ranked
    ]


def _go_live_readiness(
    *,
    rows: list[dict],
    loop_running: bool,
    loop_started_at_ms: int | None,
    env: dict[str, str],
    now_ms: int,
) -> list[dict]:
    """Return the 11-item path-to-live checklist with auto-detected status.

    Each item: {key, name, status: "done"|"todo"|"in_progress", detail}.
    Pure function — easy to test, easy to render.
    """
    from risk.live_gate import (  # noqa: PLC0415
        CALIBRATION_TRADE_COUNT,
        is_live_trading_enabled,
    )

    checks: list[dict] = []

    live_flag = is_live_trading_enabled(env)
    checks.append(
        {
            "key": "live_flag",
            "name": "LIVE_TRADING env flag",
            "status": "done" if live_flag else "todo",
            "detail": (
                "Set to 'true' — real broker construction allowed."
                if live_flag
                else "Not set — bot stays paper-only. Set LIVE_TRADING=true "
                "in .env when you're ready to wire a real broker."
            ),
        }
    )

    has_kraken = False
    try:
        import importlib.util  # noqa: PLC0415

        has_kraken = importlib.util.find_spec("execution.ccxt_kraken") is not None
    except Exception:  # noqa: BLE001
        has_kraken = False
    checks.append(
        {
            "key": "real_broker",
            "name": "Real broker adapter (Kraken via CCXT)",
            "status": "done" if has_kraken else "todo",
            "detail": (
                "execution/ccxt_kraken.py present."
                if has_kraken
                else "Only PaperBroker exists. Next session: build "
                "KrakenBroker implementing the same Broker Protocol."
            ),
        }
    )

    has_reconcile = False
    try:
        import importlib.util  # noqa: PLC0415

        has_reconcile = importlib.util.find_spec("execution.reconcile") is not None
    except Exception:  # noqa: BLE001
        has_reconcile = False
    last_reconcile = next(
        (r for r in reversed(rows) if r["event_type"] == "reconcile"),
        None,
    )
    if has_reconcile and last_reconcile is not None:
        reconcile_status = "done"
        reconcile_detail = f"Last run on loop start: {last_reconcile['rationale'] or 'no detail'}"
    elif has_reconcile:
        reconcile_status = "done"
        reconcile_detail = (
            "execution/reconcile.py present. Runs on every build_from_env() — "
            "first run will appear in the decision log next loop start."
        )
    else:
        reconcile_status = "todo"
        reconcile_detail = (
            "Pulls open orders + positions from broker on start, halts "
            "new orders on mismatch. Implement after Kraken broker."
        )
    checks.append(
        {
            "key": "reconcile",
            "name": "Reconcile-on-startup (P-11)",
            "status": reconcile_status,
            "detail": reconcile_detail,
        }
    )

    has_human_gate = False
    gate_state_str = ""
    try:
        import importlib.util  # noqa: PLC0415

        has_human_gate = importlib.util.find_spec("risk.human_gate") is not None
    except Exception:  # noqa: BLE001
        has_human_gate = False
    if has_human_gate:
        from risk.human_gate import DEFAULT_TOKEN_PATH, get_session_state  # noqa: PLC0415

        gs = get_session_state(DEFAULT_TOKEN_PATH)
        if gs.is_active:
            remaining_h = (gs.seconds_remaining or 0) // 3600
            gate_state_str = f"ACTIVE — {remaining_h}h remaining"
            human_gate_status = "done"
        else:
            gate_state_str = "INACTIVE — live broker refuses to start"
            human_gate_status = "done"  # infrastructure exists, just not currently engaged
    else:
        human_gate_status = "todo"
        gate_state_str = "not built"
    checks.append(
        {
            "key": "human_gate",
            "name": "Per-session human 'go live' gate",
            "status": human_gate_status,
            "detail": (
                f"Sidebar widget — type LIVE to activate, expires after 24h. "
                f"Current state: {gate_state_str}. Required for non-dry-run "
                f"Kraken broker; orthogonal to the env flag."
            ),
        }
    )

    fills = [r for r in rows if r["event_type"] == "order_filled"]
    checks.append(
        {
            "key": "calibration",
            "name": f"Calibration mode (first {CALIBRATION_TRADE_COUNT} trades tagged)",
            "status": "done",
            "detail": (
                f"Executor writes mode=… into every fill's metadata_json. "
                f"Live broker will use 'live_calibration' for the first "
                f"{CALIBRATION_TRADE_COUNT} trades (S-55), then 'live'. "
                f"Currently {len(fills)} fills logged."
            ),
        }
    )

    tg_token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = env.get("TELEGRAM_CHAT_ID", "").strip()
    tg_ok = bool(tg_token and tg_chat)
    checks.append(
        {
            "key": "telegram",
            "name": "Telegram alerts configured (critical for live)",
            "status": "done" if tg_ok else "todo",
            "detail": (
                "Bot can notify you of fills, kill-switch events, and crashes."
                if tg_ok
                else "Not configured. Sidebar → Telegram alerts → enter token "
                "+ chat ID. Optional for paper, mandatory before live."
            ),
        }
    )

    has_live_caps = False
    cap_detail = ""
    try:
        from risk.caps import live_calibration_caps  # noqa: PLC0415

        has_live_caps = True
        # Compute caps for the operator's actual initial_cash so the UI shows
        # the *real* numbers their bot will use.
        try:
            initial_cash_for_panel = float(env.get("TRADERBOT_INITIAL_CASH", "100") or "100")
        except (TypeError, ValueError):
            initial_cash_for_panel = 100.0
        cal = live_calibration_caps(initial_cash_usd=initial_cash_for_panel)
        cap_detail = (
            f"Live calibration preset @ ${initial_cash_for_panel:.0f} portfolio: "
            f"max ${cal.max_position_notional_usd:.2f}/trade · "
            f"daily-loss kill ${cal.max_daily_loss_usd:.2f} · "
            f"DD halt {cal.max_daily_drawdown_pct * 100:.1f}% · "
            f"{cal.max_concurrent_positions} concurrent."
        )
    except Exception:  # noqa: BLE001
        has_live_caps = False
    checks.append(
        {
            "key": "live_caps",
            "name": "Hardened risk caps for live (proportional to portfolio size)",
            "status": "done" if has_live_caps else "todo",
            "detail": (
                cap_detail
                if has_live_caps
                else "Existing caps are paper-tuned. Live needs absolute-dollar "
                "position cap (not just %), tighter daily-DD kill, and a "
                "first-trade-of-day delay so a bad open doesn't wipe out."
            ),
        }
    )

    try:
        from tools.system_check import check_readiness as _mac_check  # noqa: PLC0415

        mac_report = _mac_check()
        if not mac_report.is_macos or not mac_report.pmset_available:
            mac_status = "todo"
            mac_detail = mac_report.summary
        elif mac_report.is_clean:
            mac_status = "done"
            settings_str = ", ".join(f"{c.name}={c.actual}" for c in mac_report.checks)
            mac_detail = f"All 5 settings correct: {settings_str}."
        else:
            mac_status = "todo"
            failing = ", ".join(
                f"{c.name}={c.actual}→{c.expected}" for c in mac_report.checks if not c.passed
            )
            mac_detail = f"{failing}. Run: <code>{mac_report.remediation}</code>"
    except Exception as e:  # noqa: BLE001
        mac_status = "todo"
        mac_detail = f"check failed: {type(e).__name__}: {e}"
    checks.append(
        {
            "key": "mac_mini",
            "name": "Mac Mini 24/7 prep (pmset settings)",
            "status": mac_status,
            "detail": mac_detail,
        }
    )

    soak_target_s = 7 * 24 * 3600
    if loop_started_at_ms and loop_running:
        soak_elapsed_s = max(0, (now_ms - loop_started_at_ms) // 1000)
        soak_pct = min(100, soak_elapsed_s * 100 // soak_target_s)
        soak_h = soak_elapsed_s // 3600
        if soak_elapsed_s >= soak_target_s:
            soak_status = "done"
            soak_detail = f"7-day soak complete — {soak_h}h elapsed."
        else:
            soak_status = "in_progress"
            soak_detail = (
                f"{soak_h}h / 168h elapsed ({soak_pct}%). Bot must run "
                f"continuously without crashes or stuck signals."
            )
    else:
        soak_status = "todo"
        soak_detail = "Loop not running — start it from the sidebar."
    checks.append(
        {
            "key": "soak",
            "name": "7-day continuous paper soak (Phase 1 final task)",
            "status": soak_status,
            "detail": soak_detail,
        }
    )

    checks.append(
        {
            "key": "decision_log",
            "name": "Decision log audit trail (append-only SQLite)",
            "status": "done",
            "detail": f"{len(rows):,} rows logged. UPDATE/DELETE blocked by triggers.",
        }
    )

    checks.append(
        {
            "key": "coid",
            "name": "Idempotent client_order_id",
            "status": "done",
            "detail": (
                "make_client_order_id(strategy_id, signal_id) gives every "
                "order a deterministic ID — retries can't double-fill."
            ),
        }
    )

    return checks


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
            line={"color": "#6b6b6b", "width": 1, "dash": "dot"},
            annotation_text=f"Start ${initial_cash:,.0f}",
            annotation_position="top right",
            annotation_font_color="#6b6b6b",
            annotation_font_size=10,
        )
        fig.add_annotation(
            text="Waiting for the first trade — no fills yet.",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font={"color": "#6b6b6b", "size": 13},
        )
        fig.update_xaxes(visible=False)
        fig.update_yaxes(
            range=[initial_cash * 0.95, initial_cash * 1.05],
            tickprefix="$",
            tickformat=",.0f",
            gridcolor="#1a1a1a",
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
                line={"color": "#fbbf24", "width": 1.8, "shape": "hv"},
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
        paper_bgcolor="#0e0e0e",
        plot_bgcolor="#0e0e0e",
        xaxis={
            "gridcolor": "#1a1a1a",
            "showspikes": True,
            "spikemode": "across",
            "spikecolor": "#2a2a2a",
            "spikethickness": 1,
        },
        # autorange=True + tight padding lets the chart zoom to the actual range,
        # not a forced [$0, max] which makes small moves invisible.
        yaxis={
            "gridcolor": "#1a1a1a",
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
            "font": {"size": 10, "color": "#a8a8a8"},
        },
        hovermode="x unified",
    )
    return fig


def _trades_table_html(trades: pd.DataFrame) -> str:
    """One row per closed round-trip: exit time, symbol, qty, entry/exit, net P&L, reason."""
    if trades.empty:
        return '<div class="muted" style="padding:14px;">No completed trades yet.</div>'

    rows_html: list[str] = []
    for _, t in trades.tail(25).iloc[::-1].iterrows():
        sym = str(t.get("symbol") or "—")
        pnl = float(t["pnl"])
        ret = float(t["return_pct"]) * 100
        reason = (t["exit_reason"] or "exit").replace("_", " ")
        pnl_cls = "pos" if pnl > 0 else ("neg" if pnl < 0 else "muted")
        rows_html.append(
            "<tr>"
            f'<td class="muted">{_ts_to_str(int(t["exit_ts"]), "%m-%d %H:%M")}</td>'
            f"<td><strong>{sym}</strong></td>"
            f'<td class="num">{float(t["qty"]):.6f}</td>'
            f'<td class="num">${float(t["entry_price"]):,.2f}</td>'
            f'<td class="num">${float(t["exit_price"]):,.2f}</td>'
            f'<td class="num {pnl_cls}">{pnl:+,.2f}</td>'
            f'<td class="num {pnl_cls}">{ret:+.2f}%</td>'
            f'<td class="muted">{reason}</td>'
            "</tr>"
        )
    return (
        '<table class="t">'
        "<thead><tr>"
        "<th>EXIT</th><th>SYMBOL</th>"
        '<th class="num">QTY</th><th class="num">ENTRY</th><th class="num">EXIT</th>'
        '<th class="num">NET P&amp;L</th><th class="num">RET</th><th>REASON</th>'
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
        return (
            '<div class="feed" style="border:1px solid #1a1a1a;">'
            'No actionable events yet — only <span class="muted">hold</span> signals '
            "(normal between bar closes).</div>"
        )
    return (
        '<div class="feed" style="border:1px solid #1a1a1a; max-height:340px;">'
        + "".join(lines)
        + "</div>"
    )


def _render_compare_tab() -> None:
    """Backtest comparison — runs `backtest.compare` in-process and shows the chart."""
    st.markdown(
        '<div class="muted" style="margin-bottom:8px;">Run a strategy across '
        "multiple symbols and compare equity curves vs buy-and-hold. "
        "Backfills are reused if the local parquet already covers the window.</div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns([3, 1, 1, 2])
    symbols_str = c1.text_input(
        "Symbols (comma-separated)",
        ", ".join(DEFAULT_SYMBOLS),
        key="compare_symbols",
    )
    days = c2.number_input("Days", min_value=7, max_value=180, value=30, step=1, key="compare_days")
    timeframe = c3.selectbox("Timeframe", ["1h", "4h", "1d"], index=0, key="compare_tf")
    strategy_label = c4.selectbox(
        "Strategy",
        [e.label for e in STRATEGIES.values()],
        index=0,
        key="compare_strategy",
    )

    if st.button("Run comparison", type="primary"):
        symbols = [s.strip() for s in symbols_str.split(",") if s.strip()]
        if not symbols:
            st.error("Need at least one symbol.")
            return
        entry = strategy_by_label(strategy_label)
        with st.spinner(f"Backfilling + backtesting {len(symbols)} symbols on {strategy_label}..."):
            try:
                results = run_comparison(
                    symbols,
                    days=int(days),
                    timeframe=timeframe,
                    strategy_fn=entry.fn,
                )
            except Exception as e:
                st.error(f"Comparison failed: {type(e).__name__}: {e}")
                return
        st.session_state["compare_results"] = results
        st.session_state["compare_results_strategy"] = strategy_label

    results = st.session_state.get("compare_results")
    if results is None:
        st.info("Set parameters and click **Run comparison**.")
        return

    st.plotly_chart(make_figure(results), config={"displayModeBar": False}, width="stretch")

    shown_strategy = st.session_state.get("compare_results_strategy", "Baseline EMA-cross")
    st.markdown(
        f'<div class="section-title">Per-symbol breakdown · {shown_strategy}</div>',
        unsafe_allow_html=True,
    )
    # Use the desk's .t table class — same JetBrains Mono / Oswald look as
    # Trade history. Strategy + Diff get pos/neg color (real outcome). Sharpe,
    # MaxDD, B&H are *measurements*, not outcomes — kept neutral per color rule.
    rows_html: list[str] = []
    for r in results:
        m = r.result.metrics
        strat_pct = m["total_return_pct"] * 100
        diff = strat_pct - r.buy_hold_return_pct
        diff_cls = "pos" if diff > 0 else ("neg" if diff < 0 else "muted")
        strat_cls = "pos" if strat_pct > 0 else ("neg" if strat_pct < 0 else "muted")
        exp = float(m.get("expectancy", 0.0))
        exp_cls = "pos" if exp > 0 else ("neg" if exp < 0 else "muted")
        pf = float(m.get("profit_factor", 0.0))
        pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
        pf_cls = "pos" if pf > 1.5 else ("neg" if pf < 1.0 else "muted")
        rows_html.append(
            f"<tr>"
            f"<td><strong>{r.symbol}</strong></td>"
            f'<td class="num">{r.bars}</td>'
            f'<td class="num">{int(m["num_trades"])}</td>'
            f'<td class="num">{m["win_rate"] * 100:.1f}%</td>'
            f'<td class="num {exp_cls}">${exp:+.2f}</td>'
            f'<td class="num {pf_cls}">{pf_str}</td>'
            f'<td class="num {strat_cls}"><strong>{strat_pct:+.2f}%</strong></td>'
            f'<td class="num muted">{r.buy_hold_return_pct:+.2f}%</td>'
            f'<td class="num {diff_cls}">{diff:+.2f}pp</td>'
            f'<td class="num muted">{m["sharpe"]:+.2f}</td>'
            f'<td class="num muted">{m["max_drawdown"] * 100:.2f}%</td>'
            f"</tr>"
        )
    st.markdown(
        '<table class="t">'
        "<thead><tr>"
        "<th>Symbol</th><th>Bars</th><th>Trades</th><th>WR</th>"
        "<th>Exp/trade</th><th>PF</th>"
        "<th>Strategy</th><th>B&amp;H</th><th>Diff</th>"
        "<th>Sharpe</th><th>MaxDD</th>"
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
    st.markdown(FONT_LINK + CSS, unsafe_allow_html=True)
    # Full-page reload: must run in parent frame; guard so Streamlit remounts do not stack timers.
    ms = int(REFRESH_INTERVAL_S * 1000)
    components.html(
        f"<script>"
        f"try {{"
        f"  var w = window.parent || window.top || window;"
        f"  if (!w.__traderbot_dash_reload_scheduled) {{"
        f"    w.__traderbot_dash_reload_scheduled = true;"
        f"    setTimeout(function() {{ w.location.reload(); }}, {ms});"
        f"  }}"
        f"}} catch (e) {{}}"
        f"</script>",
        height=0,
        width=0,
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
        f'<div class="wolf-header">'
        f'<div style="display:flex; justify-content:space-between; align-items:flex-start; '
        f'flex-wrap:wrap; gap:16px;">'
        f"<div>"
        f'<div class="wolf-main">Wolf of Vibe Street</div>'
        f'<div class="wolf-tagline">Execution desk · Paper session · Full tape</div>'
        f'<div style="margin-top:10px;">'
        f'<span class="wolf-dash-label">Dashboard</span>'
        f'<span class="mode {mode_cls}">{mode_text}</span>'
        f'<span class="wolf-build">BUILD {DASHBOARD_BUILD}</span>'
        f"</div></div>"
        f'<div class="status" style="display:flex; gap:14px; flex-wrap:wrap; '
        f'justify-content:flex-end; text-align:right;">'
        f'<span><span style="color:#6b6b6b;">SYMBOL</span> '
        f'<span class="v">{symbol_env} · {timeframe}</span></span>'
        f'<span><span style="color:#6b6b6b;">NEXT BAR</span> '
        f'<span class="v">{next_bar_str}</span></span>'
        f'<span class="v">{now_utc_str}</span>'
        f'<span><span style="color:#6b6b6b;">STATUS</span> '
        f'<span class="v {status_cls}">{status_v}</span></span>'
        f"</div></div></div>",
        unsafe_allow_html=True,
    )

    if not rows:
        st.warning(
            f"No decision log yet at `{log_path}`. Start the live loop "
            f"(`uv run python -m workers.live_loop`) — page auto-refreshes."
        )

    tab_overview, tab_compare = st.tabs(["DESK", "COMPARE"])

    with tab_overview:
        # Soak health — green/yellow/red banner so a morning check is one glance.
        loop_status = loop_control.status()
        checks = soak_health(
            rows,
            bot_running=loop_status.running,
            kill_switch_on=kill_switch_active(kill_switch_path),
            now_ms=int(pd.Timestamp.utcnow().timestamp() * 1000),
            expected_bar_seconds=tf_seconds,
        )
        worst = "ok"
        for c in checks:
            if c["status"] == "error":
                worst = "error"
                break
            if c["status"] == "warn" and worst != "error":
                worst = "warn"
        banner_color = {"ok": "#22c55e", "warn": "#fbbf24", "error": "#ef4444"}[worst]
        banner_label = {"ok": "HEALTHY", "warn": "ATTENTION", "error": "ISSUES"}[worst]
        st.markdown(
            f'<div style="background:linear-gradient(180deg,#121212 0%,#0c0c0c 100%); '
            f"border:1px solid #1a1a1a; border-left:2px solid {banner_color}; "
            f'padding:10px 14px; margin-bottom:10px;">'
            f'<div style="display:flex; justify-content:space-between; align-items:center;">'
            f'<span style="font-family:Oswald,sans-serif; font-size:10px; color:#6b6b6b; '
            f'text-transform:uppercase; letter-spacing:0.2em; font-weight:600;">'
            f"Soak status</span>"
            f"<span style=\"font-family:'JetBrains Mono',monospace; "
            f"color:{banner_color}; font-weight:700; font-size:11px; "
            f'letter-spacing:0.1em;">{banner_label}</span></div>'
            f'<div style="margin-top:8px; display:grid; '
            f"grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:4px 16px; "
            f"font-family:'JetBrains Mono',monospace; font-size:10px;\">"
            + "".join(
                '<div><span style="color:'
                + {"ok": "#22c55e", "warn": "#fbbf24", "error": "#ef4444"}[c["status"]]
                + '; font-weight:700;">['
                + c["status"].upper()[:3]
                + "]</span> "
                f'<span style="color:#f0f0f0;">{c["name"]}</span> '
                f'<span style="color:#6b6b6b;">— {c["message"]}</span></div>'
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

        # 4 cards (not 5) — "Total P&L" was redundant with Equity-card delta. With
        # the sidebar open the 5-column layout broke "$10,000.00" across 3 lines;
        # 4 columns + clamp()'d font keeps values on one line down to ~480px wide.
        k1, k2, k3, k4 = st.columns(4)
        delta_eq = current_equity - initial_cash
        k1.markdown(
            _kpi(
                _sign(delta_eq),
                "Equity",
                f"${current_equity:,.2f}",
                f"{total_return * 100:+.2f}% · ${delta_eq:+,.2f}",
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
                "since 00:00 UTC",
            ),
            unsafe_allow_html=True,
        )
        k4.markdown(
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

        neutral = "#a8a8a8"

        def _mini(label: str, value: str, color: str = "#f0f0f0") -> str:
            return (
                f'<div class="kpi-mini">'
                f'<div class="lbl">{label}</div>'
                f'<div class="val" style="color:{color};">{value}</div>'
                f"</div>"
            )

        if sharpe_v is None:
            sharpe_str, sharpe_color = "—", neutral
        else:
            sharpe_str = f"{sharpe_v:+.2f}"
            sharpe_color = "#22c55e" if sharpe_v > 0 else ("#ef4444" if sharpe_v < 0 else "#f0f0f0")
        if maxdd_v is None:
            maxdd_str, maxdd_color = "—", neutral
        elif maxdd_v == 0:
            maxdd_str, maxdd_color = "0.00%", neutral
        else:
            maxdd_str, maxdd_color = f"{maxdd_v * 100:.2f}%", "#ef4444"
        if win_rate_v is None:
            wr_str, wr_color = "—", neutral
        else:
            wr_str = f"{win_rate_v:.1f}%"
            wr_color = "#22c55e" if win_rate_v >= 33.3 else "#ef4444"
        # Exposure = market value / equity (not the same as risk caps; label clearly).
        if exposure_pct == 0:
            exp_str, exp_color = f"0% · max {max_pos} pos", neutral
        else:
            exp_str, exp_color = f"{exposure_pct:.1f}% · max {max_pos} pos", "#fbbf24"

        m1, m2, m3, m4 = st.columns(4)
        m1.markdown(_mini("Sharpe (ann.)", sharpe_str, sharpe_color), unsafe_allow_html=True)
        m2.markdown(_mini("Max drawdown", maxdd_str, maxdd_color), unsafe_allow_html=True)
        m3.markdown(_mini("Win rate", wr_str, wr_color), unsafe_allow_html=True)
        m4.markdown(_mini("Exposure (notional)", exp_str, exp_color), unsafe_allow_html=True)

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
                        f"padding:4px 0; font-family:'JetBrains Mono',monospace; "
                        f'font-size:11px;">'
                        f'<span style="color:#fbbf24;">{reason}</span>'
                        f'<span style="color:#6b6b6b;">{count}</span></div>',
                        unsafe_allow_html=True,
                    )

        # --- Row 3: Symbol expectancy ranker (cached 24h, per strategy) ---
        # Decision support: shows whether the symbol the bot is trading right now
        # actually has the best edge among the watchlist. Strategy toggle lets
        # you A/B baseline EMA-cross vs mean-reversion RSI on the same data.
        # Backtests are heavy so the cache is per-day per-strategy; refresh
        # button below clears it on demand.
        labels = [e.label for e in STRATEGIES.values()]
        default_label = STRATEGIES[DEFAULT_STRATEGY_ID].label
        chosen_label = st.session_state.get("expectancy_strategy", default_label)
        if chosen_label not in labels:
            chosen_label = default_label
        _section(
            "Symbol expectancy",
            f"{chosen_label} · 30d backtest · {timeframe} bars",
        )
        ctl_left, _ = st.columns([2, 3])
        chosen_label = ctl_left.selectbox(
            "Strategy",
            labels,
            index=labels.index(chosen_label),
            key="expectancy_strategy",
            label_visibility="collapsed",
        )
        chosen_entry = strategy_by_label(chosen_label)
        live_symbol = symbol_env
        try:
            ranked = _cached_symbol_ranking(
                DEFAULT_SYMBOLS, days=30, timeframe=timeframe, strategy_id=chosen_entry.id
            )
        except Exception as e:  # noqa: BLE001
            ranked = []
            st.markdown(
                f'<div style="font-family:JetBrains Mono,monospace; font-size:11px; '
                f'color:var(--red); padding:8px 0;">Ranking failed: '
                f"{type(e).__name__}: {e}</div>",
                unsafe_allow_html=True,
            )

        if ranked:
            rows_html: list[str] = []
            for i, r in enumerate(ranked):
                m = r["metrics"]
                exp = float(m.get("expectancy", 0.0))
                pf = float(m.get("profit_factor", 0.0))
                wr = float(m.get("win_rate", 0.0)) * 100
                tot_pct = float(m.get("total_return_pct", 0.0)) * 100
                n_tr = int(m.get("num_trades", 0))
                live_marker = "★" if r["symbol"] == live_symbol else ""
                rank_marker = ["#1", "#2", "#3"][i] if i < 3 else f"#{i + 1}"
                exp_cls = "pos" if exp > 0 else ("neg" if exp < 0 else "muted")
                pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
                pf_cls = "pos" if pf > 1.5 else ("neg" if pf < 1.0 else "muted")
                tot_cls = "pos" if tot_pct > 0 else ("neg" if tot_pct < 0 else "muted")
                rows_html.append(
                    f"<tr>"
                    f'<td class="muted" style="width:32px;">{rank_marker}</td>'
                    f"<td><strong>{r['symbol']}</strong> "
                    f'<span style="color:var(--accent); font-weight:700;">{live_marker}</span>'
                    f"</td>"
                    f'<td class="num">{n_tr}</td>'
                    f'<td class="num {exp_cls}"><strong>${exp:+.2f}</strong></td>'
                    f'<td class="num {pf_cls}">{pf_str}</td>'
                    f'<td class="num">{wr:.1f}%</td>'
                    f'<td class="num {tot_cls}">{tot_pct:+.2f}%</td>'
                    f"</tr>"
                )
            st.markdown(
                '<table class="t">'
                "<thead><tr>"
                "<th></th><th>Symbol</th><th>Trades</th>"
                "<th>Expectancy / trade</th><th>Profit factor</th>"
                "<th>Win rate</th><th>Total return</th>"
                "</tr></thead><tbody>" + "".join(rows_html) + "</tbody></table>",
                unsafe_allow_html=True,
            )

            # Decision support — three cases, only the first one is "switch symbol":
            #   1. The top symbol is profitable AND beats the live symbol → suggest switch.
            #   2. ALL symbols negative → it's a strategy problem, not a symbol problem.
            #   3. Live symbol is already #1 → say nothing (no nag).
            top = ranked[0]
            top_exp = float(top["metrics"].get("expectancy", 0.0))
            top_n = int(top["metrics"].get("num_trades", 0))
            all_negative = all(
                float(r["metrics"].get("expectancy", 0.0)) <= 0
                for r in ranked
                if int(r["metrics"].get("num_trades", 0)) > 0
            )
            live_row = next(
                (r for r in ranked if r["symbol"] == live_symbol),
                None,
            )

            hint_html: str | None = None
            if all_negative and top_n > 0:
                hint_html = (
                    f'<span style="color:var(--red); font-weight:700;">STRATEGY ALERT</span> '
                    f"All {len(ranked)} watchlist symbols have negative expectancy on "
                    f"<strong>{chosen_label}</strong> over the last 30d. Switching symbol won't "
                    f"fix this — try the other strategy in the dropdown above, "
                    f"or enable the LLM filter."
                )
            elif (
                top["symbol"] != live_symbol and top_n > 0 and top_exp > 0 and live_row is not None
            ):
                delta_exp = top_exp - float(live_row["metrics"].get("expectancy", 0.0))
                if delta_exp > 0:
                    hint_html = (
                        f'<span style="color:var(--accent); font-weight:700;">HINT</span> '
                        f"<strong>{top['symbol']}</strong> has "
                        f"${delta_exp:+.2f}/trade higher expectancy than "
                        f"<strong>{live_symbol}</strong> over the last 30d. "
                        f"Switch via <code>TRADERBOT_SYMBOL</code> in <code>.env</code> "
                        f"and restart the loop."
                    )

            if hint_html:
                st.markdown(
                    f'<div style="margin-top:10px; padding:8px 12px; '
                    f"background:rgba(251,191,36,0.06); border-left:2px solid "
                    f"var(--accent); font-family:JetBrains Mono,monospace; "
                    f'font-size:11px; color:var(--text-2);">{hint_html}</div>',
                    unsafe_allow_html=True,
                )

            cols = st.columns([1, 4])
            if cols[0].button("Refresh ranking", width="stretch"):
                _cached_symbol_ranking.clear()
                st.rerun()
            cols[1].markdown(
                '<div style="font-family:JetBrains Mono,monospace; font-size:10px; '
                'color:var(--text-3); padding-top:10px;">'
                "Backtest costs: 10 bps commission + 5 bps slippage. "
                "★ = symbol the live loop is currently trading."
                "</div>",
                unsafe_allow_html=True,
            )

        # --- Row 4: Go-Live readiness checklist ---
        # The 11-point path from paper → real money. Auto-detected status per
        # item; manual steps are flagged with a clear "what to do" detail line.
        # Order matches Phase 3 of the implementation plan.
        readiness = _go_live_readiness(
            rows=rows,
            loop_running=loop_status.running,
            loop_started_at_ms=loop_status.started_at_ms,
            env=env_config.read_env(),
            now_ms=int(pd.Timestamp.utcnow().timestamp() * 1000),
        )
        n_done = sum(1 for c in readiness if c["status"] == "done")
        n_total = len(readiness)
        _section(
            "Go-Live readiness",
            f"{n_done} / {n_total} done · path from paper → real money",
        )
        status_glyph = {"done": "✓", "in_progress": "⏳", "todo": "○"}
        status_color = {
            "done": "var(--green)",
            "in_progress": "var(--accent)",
            "todo": "var(--text-3)",
        }
        rows_html: list[str] = []
        for i, c in enumerate(readiness, start=1):
            glyph = status_glyph[c["status"]]
            color = status_color[c["status"]]
            name_color = "var(--text)" if c["status"] != "todo" else "var(--text-2)"
            rows_html.append(
                f'<tr style="border-bottom:1px solid var(--border);">'
                f'<td style="padding:8px 10px; width:28px; color:var(--text-3); '
                f"font-family:'JetBrains Mono',monospace; font-size:11px; "
                f'vertical-align:top;">#{i:02d}</td>'
                f'<td style="padding:8px 10px; width:24px; color:{color}; '
                f"font-family:'JetBrains Mono',monospace; font-size:14px; "
                f'font-weight:700; vertical-align:top;">{glyph}</td>'
                f'<td style="padding:8px 10px; vertical-align:top;">'
                f"<div style=\"font-family:'Oswald',sans-serif; font-size:12px; "
                f"font-weight:600; letter-spacing:0.06em; color:{name_color}; "
                f'text-transform:uppercase;">{c["name"]}</div>'
                f"<div style=\"font-family:'JetBrains Mono',monospace; "
                f"font-size:11px; color:var(--text-3); margin-top:4px; "
                f'line-height:1.5;">{c["detail"]}</div>'
                f"</td></tr>"
            )
        st.markdown(
            f'<table style="width:100%; border-collapse:collapse; '
            f"background:linear-gradient(180deg,#0e0e0e 0%,#0a0a0a 100%); "
            f'border:1px solid var(--border);">'
            f"<tbody>{''.join(rows_html)}</tbody></table>",
            unsafe_allow_html=True,
        )

        # --- Footer disclaimer ---
        last_refresh = pd.Timestamp.utcnow().strftime("%H:%M:%S UTC")
        st.markdown(
            f'<div class="footer">'
            f"<div>Paper execution · Testing only · Not financial advice</div>"
            f'<div class="mono">Last refresh · {last_refresh}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )

    with tab_compare:
        _render_compare_tab()

    with st.sidebar:
        with st.expander("Ser du inte ändringar? (felsök)", expanded=False):
            st.markdown(
                f"- **Build som ska synas i headern:** `{DASHBOARD_BUILD}`\n"
                f"- **Projektrot:** `{_PROJECT_ROOT}`\n"
                "- Kör alltid från mappen **`traderbot`**:  \n"
                "  `cd …/traderbot && uv run streamlit run ui/dashboard.py`\n"
                "- Stoppa gammal Streamlit (annars laddas gammal kod):  \n"
                "  `pkill -f 'streamlit run ui/dashboard'` eller byt port:  \n"
                "  `TRADERBOT_PORT=8502 uv run streamlit run ui/dashboard.py --server.port 8502`\n"
                "- **Hård omladdning i webbläsaren:** Cmd+Shift+R (Mac) / Ctrl+Shift+R (Win)\n"
                "- Rensa cache: Streamlit-menyn **⋮ → Clear cache** (om du ser den)."
            )
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

        # Active strategy — read from .env so it reflects what the *next* loop
        # start will use (and matches the running loop if .env hasn't changed
        # since start). Surfaces the multi-strategy switch to the operator.
        env_now = env_config.read_env()
        active_strategy_id = (
            env_now.get("TRADERBOT_STRATEGY")
            or env_now.get("TRADERBOT_STRATEGY_ID")
            or DEFAULT_STRATEGY_ID
        )
        if active_strategy_id in STRATEGIES:
            active_label = STRATEGIES[active_strategy_id].label
            strategy_color = "var(--accent)"
        else:
            active_label = f"⚠ unknown: {active_strategy_id}"
            strategy_color = "var(--red)"
        st.markdown(
            f'<div style="font-family:JetBrains Mono,monospace; font-size:11px; '
            f'color:var(--text-3); margin-top:8px;">'
            f'<span style="color:var(--text-3);">Strategy</span> '
            f'<span style="color:{strategy_color}; font-weight:700;">{active_label}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )
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
        st.subheader("LIVE SESSION GATE")
        from risk.human_gate import (  # noqa: PLC0415
            DEFAULT_TOKEN_PATH,
            LIVE_CONFIRMATION_PHRASE,
            MAX_SESSION_AGE_S,
            activate_live_session,
            deactivate_live_session,
            get_session_state,
        )

        gate_state = get_session_state(DEFAULT_TOKEN_PATH)
        if gate_state.is_active:
            remaining_h = (gate_state.seconds_remaining or 0) // 3600
            remaining_m = ((gate_state.seconds_remaining or 0) % 3600) // 60
            st.success(f"ACTIVE · expires in {remaining_h}h {remaining_m:02d}m")
            if st.button("Deactivate session", type="secondary", width="stretch"):
                deactivate_live_session(DEFAULT_TOKEN_PATH)
                st.rerun()
        else:
            st.warning("INACTIVE · live broker refuses to start without this")
            confirm_input = st.text_input(
                f"Type '{LIVE_CONFIRMATION_PHRASE}' to confirm this session",
                key="live_session_confirm",
                placeholder=LIVE_CONFIRMATION_PHRASE,
            )
            can_activate = confirm_input == LIVE_CONFIRMATION_PHRASE
            if st.button(
                "Activate live session",
                type="primary",
                width="stretch",
                disabled=not can_activate,
            ):
                try:
                    activate_live_session(confirm_input, DEFAULT_TOKEN_PATH)
                    st.session_state.pop("live_session_confirm", None)
                    st.rerun()
                except ValueError as e:
                    st.error(f"Activation failed: {e}")
        st.caption(f"Token: `{DEFAULT_TOKEN_PATH}` · expires after {MAX_SESSION_AGE_S // 3600}h")

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
                f'<span style="color:#a8a8a8; font-family:JetBrains Mono,monospace; '
                f'font-size:11px;">{k}</span>'
                f'<span style="font-family:JetBrains Mono,monospace; font-size:11px; '
                f'color:#f0f0f0;">{v}</span></div>',
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
