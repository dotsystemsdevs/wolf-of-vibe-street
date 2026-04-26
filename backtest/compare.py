"""Multi-symbol baseline comparison.

Run: `uv run python -m backtest.compare`
or with custom symbols: `TRADERBOT_SYMBOLS="BTC/USDT,ETH/USDT" uv run python -m backtest.compare`

Backfills each symbol (idempotent — uses existing parquet if fresh enough), runs the
baseline strategy, prints a side-by-side table, and writes an interactive Plotly HTML
report comparing equity curves vs buy-and-hold.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from backtest.engine import BacktestConfig, BacktestResult, run_backtest
from data.backfill import backfill_ohlcv
from data.store import bars_path, load_bars, save_bars
from features.compute import bars_to_df
from signals.types import Signal
from strategies.baseline_ema_cross import generate_signals as baseline_signals
from strategies.mean_reversion_rsi import generate_signals as mean_rev_signals

DEFAULT_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
DEFAULT_TIMEFRAME = "1h"
DEFAULT_DAYS = 30
INITIAL_CASH = 10_000.0

# Strategy registry — single source of truth for "which strategies exist".
# `id` (snake_case) is what goes in .env and the decision log; `label` is what
# the dashboard dropdown shows; `fn` is the signal generator. Adding a new
# strategy here surfaces it everywhere — dashboard, live loop, decision log.
StrategyFn = Callable[..., list[Signal]]


@dataclass(frozen=True, slots=True)
class StrategyEntry:
    id: str
    label: str
    fn: StrategyFn


STRATEGIES: dict[str, StrategyEntry] = {
    "baseline_ema_cross": StrategyEntry(
        id="baseline_ema_cross",
        label="Baseline EMA-cross",
        fn=baseline_signals,
    ),
    "mean_reversion_rsi": StrategyEntry(
        id="mean_reversion_rsi",
        label="Mean-reversion RSI",
        fn=mean_rev_signals,
    ),
}
DEFAULT_STRATEGY_ID = "baseline_ema_cross"


def strategy_by_label(label: str) -> StrategyEntry:
    """Look up a strategy by its dashboard label. Raises if not found."""
    for entry in STRATEGIES.values():
        if entry.label == label:
            return entry
    raise KeyError(f"unknown strategy label: {label!r}")


def strategy_by_id(strategy_id: str) -> StrategyEntry:
    """Look up by snake_case id (used by live loop env var). Raises if not found."""
    if strategy_id not in STRATEGIES:
        known = ", ".join(STRATEGIES.keys())
        raise ValueError(
            f"unknown TRADERBOT_STRATEGY={strategy_id!r}. Known: {known}"
        )
    return STRATEGIES[strategy_id]


# Back-compat alias — older callers (and the CLI main) still import `generate_signals`.
generate_signals = baseline_signals


@dataclass(frozen=True, slots=True)
class SymbolResult:
    symbol: str
    bars: int
    result: BacktestResult
    buy_hold_return_pct: float
    first_close: float
    last_close: float


def ensure_backfill(
    symbol: str, timeframe: str, since_ms: int, *, exchange: str = "binance"
) -> pd.DataFrame:
    """Reuse existing parquet if it covers the requested window; else fetch + persist.

    `since_ms` is treated as a *floor* — if we already have data at or before that
    timestamp, we reuse it and don't re-pull from Binance.
    """
    path = bars_path(exchange, symbol, timeframe)
    if path.exists():
        existing = load_bars(path)
        if existing and int(existing[0]["timestamp_ms"]) <= since_ms:
            return bars_to_df(existing)
    bars = backfill_ohlcv(symbol, timeframe=timeframe, since_ms=since_ms)
    if not bars:
        raise RuntimeError(f"backfill returned no bars for {symbol}")
    save_bars(bars, path)
    return bars_to_df(bars)


def run_one(
    symbol: str,
    df: pd.DataFrame,
    config: BacktestConfig,
    *,
    strategy_fn: StrategyFn | None = None,
) -> SymbolResult:
    """Backtest a single symbol with the given strategy (defaults to baseline)."""
    fn = strategy_fn or baseline_signals
    sigs = fn(df, symbol=symbol)
    res = run_backtest(df, sigs, config)
    first = float(df["close"].iloc[0])
    last = float(df["close"].iloc[-1])
    return SymbolResult(
        symbol=symbol,
        bars=len(df),
        result=res,
        buy_hold_return_pct=(last / first - 1.0) * 100,
        first_close=first,
        last_close=last,
    )


def render_table(results: list[SymbolResult]) -> str:
    """Plain-text comparison table for the terminal."""
    header = (
        f"{'Symbol':<12} {'Bars':>6} {'Trades':>7} "
        f"{'WR':>7} {'Strat':>9} {'B&H':>9} {'Diff':>9} "
        f"{'Sharpe':>8} {'MaxDD':>8}"
    )
    sep = "-" * len(header)
    lines = [header, sep]
    for r in results:
        m = r.result.metrics
        strat_pct = m["total_return_pct"] * 100
        diff = strat_pct - r.buy_hold_return_pct
        lines.append(
            f"{r.symbol:<12} {r.bars:>6} {int(m['num_trades']):>7} "
            f"{m['win_rate'] * 100:>6.1f}% "
            f"{strat_pct:>+8.2f}% {r.buy_hold_return_pct:>+8.2f}% {diff:>+8.2f}pp "
            f"{m['sharpe']:>+8.2f} {m['max_drawdown'] * 100:>+7.2f}%"
        )
    return "\n".join(lines)


def make_figure(results: list[SymbolResult]) -> go.Figure:
    """Plotly figure: equity curves per symbol, normalized to % return vs starting capital."""
    fig = go.Figure()
    for r in results:
        eq = r.result.equity_curve
        ts = pd.to_datetime(eq.index, unit="ms", utc=True)
        normalized = (eq.values / INITIAL_CASH - 1.0) * 100
        fig.add_trace(
            go.Scatter(
                x=ts,
                y=normalized,
                mode="lines",
                name=f"{r.symbol} (strategy)",
                hovertemplate=f"<b>{r.symbol}</b> %{{y:+.2f}}%<extra></extra>",
            )
        )
    fig.add_hline(y=0, line={"color": "#9ca3af", "width": 1, "dash": "dot"})
    fig.update_layout(
        title="Baseline EMA-cross — equity curves vs starting capital",
        template="plotly_dark",
        height=500,
        xaxis_title="Time (UTC)",
        yaxis_title="Return %",
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
    )
    return fig


def render_html(results: list[SymbolResult], out_path: Path) -> Path:
    """Persist the figure as standalone HTML for browser-opening from CLI."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    make_figure(results).write_html(str(out_path), include_plotlyjs="cdn")
    return out_path


def run_comparison(
    symbols: list[str] | tuple[str, ...],
    *,
    days: int,
    timeframe: str = "1h",
    config: BacktestConfig | None = None,
    strategy_fn: StrategyFn | None = None,
) -> list[SymbolResult]:
    """One-shot helper for callers (e.g. the dashboard) that want a fresh comparison.

    `strategy_fn` defaults to the baseline EMA-cross. Pass any signal generator
    matching `(df, *, symbol) -> list[Signal]` to backtest a different strategy.
    """
    cfg = config or BacktestConfig(initial_cash=INITIAL_CASH)
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - days * 24 * 3600 * 1000
    out: list[SymbolResult] = []
    for sym in symbols:
        df = ensure_backfill(sym, timeframe, since_ms)
        out.append(run_one(sym, df, cfg, strategy_fn=strategy_fn))
    return out


def rank_by_expectancy(results: list[SymbolResult]) -> list[SymbolResult]:
    """Best-to-worst by per-trade expectancy ($), tie-break on Sharpe.

    Symbols with zero trades sink to the bottom — there's no edge to measure.
    Used by the dashboard's Symbol Expectancy panel to surface "should I be
    trading something else?" in one glance.
    """
    def _key(r: SymbolResult) -> tuple[int, float, float]:
        m = r.result.metrics
        n = int(m.get("num_trades", 0))
        # has_trades-flag first so symbols with 0 trades sort last regardless of
        # whatever default 0.0 their expectancy/sharpe might be.
        return (1 if n > 0 else 0, float(m.get("expectancy", 0.0)), float(m.get("sharpe", 0.0)))

    return sorted(results, key=_key, reverse=True)


def _parse_env_symbols() -> tuple[str, ...]:
    raw = os.environ.get("TRADERBOT_SYMBOLS", "").strip()
    if not raw:
        return DEFAULT_SYMBOLS
    return tuple(s.strip() for s in raw.split(",") if s.strip())


def main() -> None:
    symbols = _parse_env_symbols()
    timeframe = os.environ.get("TRADERBOT_TIMEFRAME", DEFAULT_TIMEFRAME)
    days = int(os.environ.get("TRADERBOT_DAYS", str(DEFAULT_DAYS)))
    config = BacktestConfig(
        initial_cash=INITIAL_CASH,
        risk_pct=float(os.environ.get("TRADERBOT_RISK_PCT", "0.005")),
        commission_bps=float(os.environ.get("TRADERBOT_COMMISSION_BPS", "10")),
        slippage_bps=float(os.environ.get("TRADERBOT_SLIPPAGE_BPS", "5")),
    )

    now_ms = int(time.time() * 1000)
    since_ms = now_ms - days * 24 * 3600 * 1000

    print("=" * 72)
    print(f"   Multi-symbol backtest — baseline EMA-cross, {days}d × {timeframe}")
    print("=" * 72)
    print(f"  Symbols:        {', '.join(symbols)}")
    print(f"  Initial cash:   ${INITIAL_CASH:,.0f}")
    print(f"  Risk per trade: {config.risk_pct * 100:.2f}%")
    print(
        f"  Costs:          {config.commission_bps} bps commission + {config.slippage_bps} bps slippage"
    )
    print()

    results: list[SymbolResult] = []
    for sym in symbols:
        print(f"  → {sym}: backfilling...")
        df = ensure_backfill(sym, timeframe, since_ms)
        print(f"     {len(df)} bars; running backtest...")
        results.append(run_one(sym, df, config))

    print()
    print(render_table(results))
    print()

    out_path = Path(
        os.environ.get("TRADERBOT_REPORT_PATH", "data/cache/multi_symbol_backtest.html")
    )
    out_path = render_html(results, out_path)
    print(f"  HTML report: {out_path.resolve()}")
    print(f"  Open with:   open {out_path.resolve()}")
    print("=" * 72)


if __name__ == "__main__":
    main()
