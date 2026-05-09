"""A/B analyzer for the LLM conviction multiplier.

When TRADERBOT_USE_CONVICTION=true is enabled, the Executor logs both the
raw rule-based qty AND the LLM-adjusted qty in each order_placed metadata.
This module reads the decision log and computes:

  - Counterfactual P&L: what closed trades' P&L would have been at raw_qty
  - Actual P&L: what they actually were at adjusted_qty
  - LLM cost: total $$$ spent on conviction calls
  - Net edge: actual - counterfactual - cost

The verdict is whether the LLM is paying for itself. Below threshold or
sample size too small → "wait" verdict, never a hard "kill". Operator
disables manually via env flag.

CLI:
    uv run python -m tools.conviction_analyzer
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.views import trades_dataframe  # noqa: E402

DB_PATH = ROOT / "data" / "decision_log" / "traderbot.db"


@dataclass
class ConvictionStats:
    n_evaluated: int = 0
    n_fallback: int = 0
    actual_pnl: float = 0.0
    counterfactual_pnl: float = 0.0  # at raw_qty
    total_llm_cost_usd: float = 0.0
    avg_multiplier: float = 0.0
    n_above_1: int = 0  # times LLM sized UP
    n_below_1: int = 0  # times LLM sized DOWN

    @property
    def net_edge(self) -> float:
        """Actual P&L gain minus the cost of running the LLM."""
        return self.actual_pnl - self.counterfactual_pnl - self.total_llm_cost_usd


def analyze() -> ConvictionStats:
    """Walk the decision log, pair each fill with its order_placed metadata
    (which holds raw_qty + multiplier), then with the matching exit, and
    aggregate."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM decisions").fetchall()]

    # Build COID → conviction metadata index from order_placed events.
    placed_meta: dict[str, dict] = {}
    for r in rows:
        if r["event_type"] != "order_placed":
            continue
        meta_raw = r.get("metadata_json")
        if not meta_raw:
            continue
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        except (TypeError, ValueError):
            continue
        if meta.get("conviction_mult") is None:
            continue
        coid = r.get("client_order_id")
        if coid:
            placed_meta[coid] = meta

    # Match closed trades to their entry's conviction multiplier.
    trades = trades_dataframe(rows)
    stats = ConvictionStats()
    if trades.empty or not placed_meta:
        return stats

    # Build (symbol, signal_id) → entry_fill_coid by walking fills again
    fills_index: dict[tuple[str, str], str] = {}
    for r in rows:
        if r["event_type"] != "order_filled":
            continue
        if r.get("side") not in ("buy", "short"):
            continue
        sym = r["symbol"]
        sig_id = str(r.get("signal_id") or "")
        if sym and sig_id:
            fills_index[(sym, sig_id)] = r.get("client_order_id") or ""

    multipliers: list[float] = []
    for _, t in trades.iterrows():
        # Lookup the entry's COID from the placed_meta via signal_id
        sig_id = ""
        for r in rows:
            if (
                r["event_type"] == "order_filled"
                and r["symbol"] == t["symbol"]
                and int(r["timestamp_ms"]) == int(t["entry_ts"])
                and r["side"] in ("buy", "short")
            ):
                sig_id = str(r.get("signal_id") or "")
                break
        coid = fills_index.get((t["symbol"], sig_id), "")
        meta = placed_meta.get(coid)
        if not meta:
            continue
        mult = meta.get("conviction_mult")
        if mult is None:
            continue
        raw_q = float(meta.get("raw_qty") or 0)
        actual_q = float(t["qty"])
        if raw_q <= 0 or actual_q <= 0:
            continue
        # Counterfactual P&L = scale actual P&L by raw_qty / adjusted_qty.
        # Both prices are the same; only quantity differs.
        ratio = raw_q / actual_q
        counter_pnl = float(t["pnl"]) * ratio
        stats.n_evaluated += 1
        stats.actual_pnl += float(t["pnl"])
        stats.counterfactual_pnl += counter_pnl
        if meta.get("conviction_fallback"):
            stats.n_fallback += 1
        cost = meta.get("conviction_cost_usd")
        if cost is not None:
            stats.total_llm_cost_usd += float(cost)
        multipliers.append(float(mult))
        if mult > 1.0:
            stats.n_above_1 += 1
        elif mult < 1.0:
            stats.n_below_1 += 1

    if multipliers:
        stats.avg_multiplier = sum(multipliers) / len(multipliers)
    return stats


def render_report(s: ConvictionStats) -> str:
    if s.n_evaluated == 0:
        return (
            "No closed trades with conviction-multiplier metadata yet.\n"
            "Set TRADERBOT_USE_CONVICTION=true and let trades close."
        )
    delta = s.actual_pnl - s.counterfactual_pnl
    lines = [
        f"=== LLM Conviction A/B ({s.n_evaluated} closed trades) ===",
        "",
        f"Actual P&L (LLM-adjusted qty):   ${s.actual_pnl:+.2f}",
        f"Counterfactual P&L (raw qty):    ${s.counterfactual_pnl:+.2f}",
        f"Sizing delta:                    ${delta:+.2f}",
        f"LLM cost:                        ${s.total_llm_cost_usd:.4f}",
        f"Net edge (delta - cost):         ${s.net_edge:+.2f}",
        "",
        f"Avg multiplier:                  {s.avg_multiplier:.3f}",
        f"  sized UP (>1.0):               {s.n_above_1}",
        f"  sized DOWN (<1.0):             {s.n_below_1}",
        f"  fallbacks (API errors):        {s.n_fallback}",
    ]
    if s.n_evaluated < 20:
        lines.append("")
        lines.append("⚠ Sample size <20 — verdict not yet meaningful.")
    elif s.net_edge > 0:
        lines.append("")
        lines.append(f"✓ LLM is paying for itself (+${s.net_edge:.2f}).")
    else:
        lines.append("")
        lines.append(f"✗ LLM has cost more than it's added (${s.net_edge:+.2f}).")
    return "\n".join(lines)


def main() -> int:
    print(render_report(analyze()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
