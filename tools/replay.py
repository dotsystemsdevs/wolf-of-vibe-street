"""Accelerated paper-trading replay over historical data.

Reads `.env` to get the live config (symbols + per-symbol strategy mapping),
then runs the same strategies that the live bot uses across the last N days
of bar history. Identical pipeline to live (signal → executor → broker), just
with the clock fast-forwarded.

Use this when you want to know "how would the bot have done over the last
month with the current config" without waiting a month. ~30 days of data
across 15 symbols runs in < 30 seconds vs. waiting 30 days in real time.

Run:
    uv run python -m tools.replay --days 30
    uv run python -m tools.replay --days 14 --symbols BTC/USDT,ETH/USDT
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.compare import strategy_by_id  # noqa: E402
from backtest.engine import BacktestConfig, run_backtest  # noqa: E402
from data.store import bars_path, load_bars  # noqa: E402
from features.compute import bars_to_df  # noqa: E402


def _load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _parse_symbol_strategy_map() -> dict[str, str]:
    """Parse TRADERBOT_STRATEGY_PER_SYMBOL — same format as live_loop."""
    raw = os.environ.get("TRADERBOT_STRATEGY_PER_SYMBOL", "").strip()
    if not raw:
        return {}
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            sym, sid = (p.strip() for p in pair.split(":", 1))
            if sym and sid:
                out[sym] = sid
    return out


def replay(*, days: int, symbols: list[str] | None = None, timeframe: str = "1h") -> dict:
    _load_env()
    cfg = BacktestConfig(
        initial_cash=10_000.0,
        risk_pct=float(os.environ.get("TRADERBOT_RISK_PCT", "0.005")),
        commission_bps=float(os.environ.get("TRADERBOT_COMMISSION_BPS", "10.0")),
        slippage_bps=float(os.environ.get("TRADERBOT_SLIPPAGE_BPS", "5.0")),
    )
    default_strategy = (
        os.environ.get("TRADERBOT_STRATEGY")
        or os.environ.get("TRADERBOT_STRATEGY_ID")
        or "regime_aware_dipbuy"
    )
    per_sym = _parse_symbol_strategy_map()

    if symbols is None:
        symbols_env = os.environ.get("TRADERBOT_SYMBOLS", "").strip()
        symbols = [s.strip() for s in symbols_env.split(",") if s.strip()]
        if not symbols:
            symbols = [os.environ.get("TRADERBOT_SYMBOL", "BTC/USDT")]

    cutoff_ms = int((pd.Timestamp.now("UTC") - pd.Timedelta(days=days)).timestamp() * 1000)

    print(f"=== Replay last {days} days · {len(symbols)} symbols · {timeframe} bars ===")
    print(f"{'symbol':12s} {'strategy':24s} {'trades':>6s} {'PF':>7s} {'return':>8s} {'WR':>5s} {'maxDD':>6s}")
    print("-" * 80)

    rows: list[dict] = []
    by_strategy: dict[str, list[float]] = defaultdict(list)
    total_trades = 0
    total_pnl_pct = 0.0
    valid_n = 0

    for sym in symbols:
        sid = per_sym.get(sym, default_strategy)
        try:
            strat = strategy_by_id(sid)
        except KeyError:
            print(f"{sym:12s} unknown strategy '{sid}', skipping")
            continue

        path = bars_path("binance", sym, timeframe)
        if not path.exists():
            print(f"{sym:12s} no bars on disk, skipping (run backfill first)")
            continue
        bars = load_bars(path)
        df = bars_to_df(bars)
        df = df[df["timestamp_ms"] >= cutoff_ms].reset_index(drop=True)
        if len(df) < 50:
            print(f"{sym:12s} only {len(df)} bars in window, skipping")
            continue

        sigs = strat.fn(df, symbol=sym)
        result = run_backtest(df, sigs, cfg)
        m = result.metrics
        n = int(m["num_trades"])
        pf = m["profit_factor"]
        ret = m["total_return_pct"] * 100
        wr = m["win_rate"] * 100
        max_dd = m["max_drawdown"] * 100

        pf_s = "∞" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"{sym:12s} {sid:24s} {n:>6d} {pf_s:>7s} {ret:>+7.2f}% {wr:>4.0f}% {max_dd:>5.1f}%"
        )
        rows.append(
            {"symbol": sym, "strategy": sid, "trades": n, "pf": pf, "return_pct": ret,
             "win_rate": wr, "max_dd_pct": max_dd}
        )
        by_strategy[sid].append(ret)
        total_trades += n
        total_pnl_pct += ret
        valid_n += 1

    print("-" * 80)
    print()
    print("=== Aggregate ===")
    print(f"Total trades over {days}d window: {total_trades}")
    print(f"Trades per day:                  {total_trades / days:.2f}")
    print(f"Avg return per symbol:           {total_pnl_pct / valid_n:+.2f}%" if valid_n else "")
    print()
    print("Per-strategy breakdown:")
    for sid, returns in by_strategy.items():
        avg = sum(returns) / len(returns)
        wins = sum(1 for r in returns if r > 0)
        print(f"  {sid:28s} n_symbols={len(returns):2d}  avg_ret={avg:+.2f}%  wins={wins}/{len(returns)}")
    return {
        "days": days,
        "symbols": symbols,
        "rows": rows,
        "total_trades": total_trades,
        "trades_per_day": total_trades / days if days else 0.0,
        "avg_return_pct": total_pnl_pct / valid_n if valid_n else 0.0,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=14, help="lookback window in days (default 14)")
    p.add_argument("--symbols", type=str, default="", help="comma-separated symbols (default: from .env)")
    p.add_argument("--timeframe", type=str, default="1h", help="bar timeframe (default 1h)")
    args = p.parse_args()
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    replay(days=args.days, symbols=syms, timeframe=args.timeframe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
