"""Post a periodic run summary to Telegram.

Compact, since-last-report format. The cron runs us 6×/day; without windowing
each report would show identical "today" data and feel stale. We persist the
last-report timestamp + equity to disk and report:

  - Equity now + delta vs last report
  - Open holds with unrealized %
  - Fills since last report (entries + exits)

State file: `data/state/last_report.json` — overwritten atomically each send.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.store import bars_path, load_bars  # noqa: E402
from tools.notifier import TelegramNotifier  # noqa: E402
from ui.views import open_positions, trades_dataframe  # noqa: E402

DB_PATH = ROOT / "data" / "decision_log" / "traderbot.db"
STATE_PATH = ROOT / "data" / "state" / "last_report.json"
INITIAL_CASH = 10_000.0


def _load_env_file() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _load_last_report() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_last_report(equity: float, ts_ms: int) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"equity": equity, "ts_ms": ts_ms}))
    tmp.replace(STATE_PATH)


def _last_close(symbol: str, timeframe: str) -> float | None:
    p = bars_path("binance", symbol, timeframe)
    if not p.exists():
        return None
    bars = load_bars(p)
    return float(bars[-1]["close"]) if bars else None


def _short_sym(symbol: str) -> str:
    return symbol.split("/")[0] if "/" in symbol else symbol


def _format_delta_t(ms_delta: int) -> str:
    """1740000 → '29m', 7200000 → '2h', 86400000 → '1d'."""
    minutes = ms_delta // 60_000
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes / 60
    if hours < 24:
        return f"{hours:.0f}h"
    return f"{hours / 24:.1f}d"


def build_summary() -> tuple[str, float, int]:
    """Return (message, equity_now, now_ms) — caller persists state."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM decisions").fetchall()]
    timeframe = os.environ.get("TRADERBOT_TIMEFRAME", "1h")
    symbols_env = os.environ.get("TRADERBOT_SYMBOLS", os.environ.get("TRADERBOT_SYMBOL", ""))
    symbols = [s.strip() for s in symbols_env.split(",") if s.strip()]

    # Equity reconstruction (same logic as the dashboard).
    realized = 0.0
    for r in rows:
        if r["event_type"] == "order_filled":
            qty = float(r["quantity"] or 0)
            price = float(r["price"] or 0)
            if r["side"] == "sell":
                realized += qty * price
            elif r["side"] == "cover":
                realized -= qty * price
            elif r["side"] == "short":
                realized += qty * price
            else:  # buy
                realized -= qty * price
    cash = INITIAL_CASH + realized

    positions = open_positions(rows)
    marks = {s: _last_close(s, timeframe) or 0.0 for s in symbols}
    pos_value = sum(p["qty"] * marks.get(p["symbol"], 0.0) for p in positions)
    equity = cash + pos_value
    total_pct = (equity / INITIAL_CASH - 1.0) * 100 if INITIAL_CASH else 0.0

    # Window: trades + fill events since last report timestamp. First-ever
    # report falls back to 24h window so the operator sees something.
    last_report = _load_last_report()
    now_ms = int(pd.Timestamp.now("UTC").timestamp() * 1000)
    last_ts_ms = int(last_report.get("ts_ms") or (now_ms - 24 * 3_600_000))
    last_eq = float(last_report.get("equity") or INITIAL_CASH)
    delta_eq = equity - last_eq
    delta_pct = (delta_eq / last_eq * 100) if last_eq else 0.0

    # Trades closed since last report (use exit_ts).
    trades = trades_dataframe(rows)
    window_trades: list[tuple[str, float]] = []
    if not trades.empty:
        for _, t in trades.iterrows():
            if int(t["exit_ts"]) > last_ts_ms:
                window_trades.append((t["symbol"], float(t["return_pct"]) * 100))

    # New entries since last report (fills with side=buy/short, no matching exit yet).
    new_entries: list[str] = []
    for r in rows:
        if r["event_type"] != "order_filled":
            continue
        if int(r["timestamp_ms"]) <= last_ts_ms:
            continue
        if r["side"] in ("buy", "short"):
            new_entries.append(_short_sym(r["symbol"]))

    # Compose the message
    lines: list[str] = []
    window_str = _format_delta_t(now_ms - last_ts_ms)
    delta_sign = "+" if delta_eq >= 0 else ""
    lines.append(f"Equity: ${equity:,.0f} ({total_pct:+.2f}% total)")
    lines.append(f"Last {window_str}: {delta_sign}${delta_eq:,.2f} ({delta_sign}{delta_pct:.2f}%)")
    lines.append("")

    # Holds — open positions
    if positions:
        lines.append("Holds")
        for p in positions:
            sym = _short_sym(p["symbol"])
            mark = marks.get(p["symbol"], 0.0)
            entry = float(p["avg_entry"]) or mark or 1.0
            pct = (mark / entry - 1.0) * 100 if entry else 0.0
            sign = "+" if pct >= 0 else ""
            lines.append(f"{sym} {sign}{pct:.2f}%")
        lines.append("")

    # Fills since last report (closed trades)
    lines.append(f"Trades last {window_str}")
    if window_trades:
        for sym, pct in window_trades:
            short = _short_sym(sym)
            sign = "+" if pct >= 0 else ""
            lines.append(f"{short} {sign}{pct:.2f}%")
    else:
        lines.append("(none)")

    # New entries window
    if new_entries:
        lines.append("")
        lines.append(f"New entries: {', '.join(new_entries)}")

    return "\n".join(lines), equity, now_ms


def main() -> int:
    _load_env_file()
    notifier = TelegramNotifier()
    if not notifier.configured:
        print("Telegram not configured — set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID")
        return 1
    msg, equity, now_ms = build_summary()
    notifier.notify("INFO", "Report", msg)
    _save_last_report(equity, now_ms)
    print("Sent.")
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
