"""Counterfactual A/B analysis for strategy assignments.

Phase 2 part 2. The strategy_analyzer tells us how the *actual* mapping is
performing; this module tells us how the *alternative* mappings would have
performed on the same recent data. Output: a per-symbol table showing the
current strategy's PnL vs every other registered strategy's PnL on the same
window, plus a recommendation if a different strategy clearly dominates.

This is the foundation for honest LLM A/B testing later: when we add an
LLM-as-conviction-multiplier wrapper, it gets logged as another candidate
strategy and this same tool tells us if the LLM actually helps.

Run:
    uv run python -m tools.counterfactual --days 7
    uv run python -m tools.counterfactual --days 30 --symbol BTC/USDT
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.compare import STRATEGIES, strategy_by_id  # noqa: E402
from backtest.engine import BacktestConfig, run_backtest  # noqa: E402
from data.store import bars_path, load_bars  # noqa: E402
from features.compute import bars_to_df  # noqa: E402
from tools.strategy_analyzer import parse_per_symbol_map  # noqa: E402

# Strategies worth comparing in A/B. Excludes wrappers/synthetic/test variants.
A_B_CANDIDATES = [
    "regime_aware_dipbuy",
    "union_meanrev_breakout",
    "ensemble_daytrader",
    "regime_aware_long_short",
]


@dataclass
class SymbolComparison:
    symbol: str
    current_strategy: str
    current_return_pct: float
    current_trades: int
    candidates: dict[str, dict[str, float]]  # strategy_id → {return_pct, trades, pf}
    best_strategy: str
    best_return_pct: float
    delta_vs_current: float  # best - current

    @property
    def recommend_swap(self) -> bool:
        """Strong-enough signal that swapping would help. Conservative threshold:
        ≥1pp better return AND ≥10 trades to avoid sample-size noise."""
        return (
            self.delta_vs_current >= 1.0
            and self.candidates.get(self.best_strategy, {}).get("trades", 0) >= 10
        )


def _load_env() -> None:
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def compare_symbol(
    symbol: str,
    *,
    current_strategy: str,
    days: int,
    cfg: BacktestConfig,
    candidates: list[str] = A_B_CANDIDATES,
    timeframe: str = "1h",
) -> SymbolComparison:
    """Run each candidate strategy on the same `days`-window of bars."""
    bars_p = bars_path("binance", symbol, timeframe)
    if not bars_p.exists():
        return SymbolComparison(
            symbol, current_strategy, 0.0, 0, {}, current_strategy, 0.0, 0.0
        )
    bars = load_bars(bars_p)
    df_full = bars_to_df(bars)
    cutoff_ms = int((pd.Timestamp.now("UTC") - pd.Timedelta(days=days)).timestamp() * 1000)
    df = df_full[df_full["timestamp_ms"] >= cutoff_ms].reset_index(drop=True)
    if len(df) < 50:
        return SymbolComparison(
            symbol, current_strategy, 0.0, 0, {}, current_strategy, 0.0, 0.0
        )

    cand_results: dict[str, dict[str, float]] = {}
    for sid in candidates:
        try:
            strat = strategy_by_id(sid)
        except KeyError:
            continue
        sigs = strat.fn(df, symbol=symbol)
        res = run_backtest(df, sigs, cfg)
        m = res.metrics
        pf = m.get("profit_factor", 0.0)
        cand_results[sid] = {
            "return_pct": float(m.get("total_return_pct", 0.0)) * 100,
            "trades": int(m.get("num_trades", 0)),
            "pf": float(pf) if pf != float("inf") else 99.99,
        }

    current = cand_results.get(current_strategy, {"return_pct": 0.0, "trades": 0, "pf": 0.0})
    best_sid = max(cand_results, key=lambda k: cand_results[k]["return_pct"]) if cand_results else current_strategy
    best = cand_results.get(best_sid, current)
    return SymbolComparison(
        symbol=symbol,
        current_strategy=current_strategy,
        current_return_pct=current["return_pct"],
        current_trades=int(current["trades"]),
        candidates=cand_results,
        best_strategy=best_sid,
        best_return_pct=best["return_pct"],
        delta_vs_current=best["return_pct"] - current["return_pct"],
    )


def run_counterfactual(
    *,
    days: int = 7,
    symbols_filter: list[str] | None = None,
    candidates: list[str] = A_B_CANDIDATES,
) -> list[SymbolComparison]:
    """Compare the active per-symbol mapping against all candidates over recent days."""
    _load_env()
    cfg = BacktestConfig(
        initial_cash=10_000.0,
        risk_pct=float(os.environ.get("TRADERBOT_RISK_PCT", "0.0075")),
        commission_bps=float(os.environ.get("TRADERBOT_COMMISSION_BPS", "10.0")),
        slippage_bps=float(os.environ.get("TRADERBOT_SLIPPAGE_BPS", "5.0")),
    )
    default_strat = (
        os.environ.get("TRADERBOT_STRATEGY")
        or os.environ.get("TRADERBOT_STRATEGY_ID")
        or "regime_aware_dipbuy"
    )
    per_sym = parse_per_symbol_map()

    if symbols_filter:
        symbols = symbols_filter
    else:
        symbols_env = os.environ.get("TRADERBOT_SYMBOLS", "").strip()
        symbols = [s.strip() for s in symbols_env.split(",") if s.strip()]
    if not symbols:
        return []

    results: list[SymbolComparison] = []
    for sym in symbols:
        current_sid = per_sym.get(sym, default_strat)
        cmp = compare_symbol(
            sym, current_strategy=current_sid, days=days, cfg=cfg, candidates=candidates
        )
        results.append(cmp)
    return results


def render_report(comparisons: list[SymbolComparison]) -> str:
    """Plain-text report for CLI / Telegram."""
    if not comparisons:
        return "No symbols to compare."

    lines: list[str] = []
    lines.append(
        f"{'symbol':12s}  {'current':24s}  {'cur ret':>8s}  {'best alt':24s}  {'best ret':>9s}  {'Δ':>7s}  swap?"
    )
    lines.append("-" * 102)
    swaps_recommended: list[tuple[str, str, str, float]] = []
    for c in comparisons:
        swap_str = "✓" if c.recommend_swap else ""
        if c.recommend_swap:
            swaps_recommended.append(
                (c.symbol, c.current_strategy, c.best_strategy, c.delta_vs_current)
            )
        lines.append(
            f"{c.symbol:12s}  {c.current_strategy:24s}  {c.current_return_pct:>+7.2f}%  "
            f"{c.best_strategy:24s}  {c.best_return_pct:>+8.2f}%  "
            f"{c.delta_vs_current:>+6.2f}%  {swap_str}"
        )

    lines.append("")
    if swaps_recommended:
        lines.append(f"=== {len(swaps_recommended)} swap(s) recommended ===")
        for sym, old, new, delta in swaps_recommended:
            lines.append(f"  {sym}: {old} → {new}  ({delta:+.2f}% over window)")
    else:
        lines.append("Current mapping looks optimal — no swaps recommended.")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7)
    p.add_argument(
        "--symbol", type=str, default="",
        help="restrict to single symbol (empty = all from .env)",
    )
    args = p.parse_args()
    syms = [args.symbol.strip()] if args.symbol.strip() else None
    comps = run_counterfactual(days=args.days, symbols_filter=syms)
    print(f"=== Counterfactual A/B over last {args.days} days ===")
    print()
    print(render_report(comps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
