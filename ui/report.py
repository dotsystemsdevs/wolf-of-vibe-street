"""Text-mode summary of the decision log. Run: `uv run python -m ui.report`.

Reads `data/decision_log/traderbot.db` (override with env `TRADERBOT_LOG_PATH`).
Prints high-level stats + last 10 trades. Works with or without an active live loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import os  # noqa: E402

import pandas as pd  # noqa: E402

from memory.decision_log import DecisionLog  # noqa: E402
from ui.views import summary, trades_dataframe  # noqa: E402

DEFAULT_LOG_PATH = Path("data/decision_log/traderbot.db")


def _ts(ms: int) -> str:
    return pd.to_datetime(ms, unit="ms", utc=True).strftime("%Y-%m-%d %H:%M")


def render(log_path: Path, initial_cash: float) -> str:
    if not log_path.exists():
        return f"No decision log at {log_path}. Run the live loop or a replay first."

    log = DecisionLog(log_path)
    rows = log.all()
    log.close()

    s = summary(rows, initial_cash=initial_cash)
    trades = trades_dataframe(rows)

    out: list[str] = []
    out.append("=" * 56)
    out.append("   traderbot — decision-log summary")
    out.append("=" * 56)
    out.append(f"  Log file:           {log_path}")
    out.append(f"  Rows:               {s['rows_total']:,}")
    if not trades.empty:
        out.append(
            f"  Period:             {_ts(int(trades.iloc[0]['entry_ts']))}  "
            f"→  {_ts(int(trades.iloc[-1]['exit_ts']))}"
        )
    out.append("")
    out.append("  Trades:             {:>6}".format(s["trades"]))
    out.append("  Wins / losses:      {:>6} / {}".format(s["wins"], s["losses"]))
    out.append("  Win rate:           {:>6.1f} %".format(s["win_rate"] * 100))
    out.append("  Realized P&L:       ${:>+10.2f}  (net of fees)".format(s["realized_pnl"]))
    out.append(
        "  Ending estimate:    ${:>10,.2f}  ({:+.2f} %)".format(
            s["ending_cash_estimate"], s["ending_return_pct"] * 100
        )
    )
    out.append("")
    out.append("  Events:")
    for k, v in sorted(s["events"].items()):
        out.append(f"    {k:20s}  {v:>6}")
    if s["blocks_by_reason"]:
        out.append("")
        out.append("  Risk blocks:")
        for reason, count in sorted(s["blocks_by_reason"].items(), key=lambda kv: -kv[1]):
            out.append(f"    {reason:30s}  {count:>4}")

    if not trades.empty:
        out.append("")
        out.append("  Last 10 trades:")
        out.append("  " + "-" * 78)
        out.append(
            "  {:<16}  {:<16}  {:>10}  {:>10}  {:>10}  {}".format(
                "entry", "exit", "entry $", "exit $", "pnl", "reason"
            )
        )
        for _, t in trades.tail(10).iterrows():
            out.append(
                "  {:<16}  {:<16}  {:>10.2f}  {:>10.2f}  {:>+10.2f}  {}".format(
                    _ts(int(t["entry_ts"])),
                    _ts(int(t["exit_ts"])),
                    float(t["entry_price"]),
                    float(t["exit_price"]),
                    float(t["pnl"]),
                    t["exit_reason"] or "",
                )
            )
    out.append("=" * 56)
    return "\n".join(out)


def main() -> None:
    log_path = Path(os.environ.get("TRADERBOT_LOG_PATH", str(DEFAULT_LOG_PATH)))
    initial_cash = float(os.environ.get("TRADERBOT_INITIAL_CASH", "10000"))
    print(render(log_path, initial_cash))


if __name__ == "__main__":
    main()
