"""Walk-forward cross-validation for strategy robustness.

Splits a long backtest window into N consecutive folds, runs the strategy on
each fold INDEPENDENTLY, and reports per-fold metrics + an aggregate verdict.

This is the difference between "the strategy looks good in aggregate" (could be
one lucky regime carrying the average) and "the strategy is robust" (works in most
regimes). Single-pass backtest is the former; walk-forward CV is the latter.

Verdict bar:
  - PASS: PF > 1.0 in ≥ ⌈n_folds * 0.66⌉ folds AND no fold catastrophic (PF > 0.4)
  - WEAK: PF > 1.0 in ≥ ⌈n_folds * 0.5⌉ folds
  - FAIL: otherwise

Doesn't do CPCV (combinatorial purged) — that's overkill for our scale and
deterministic strategies. We have nothing to fit so no purge/embargo needed.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from backtest.engine import BacktestConfig, run_backtest
from signals.types import Signal

StrategyFn = Callable[..., list[Signal]]


@dataclass(frozen=True, slots=True)
class FoldResult:
    fold_index: int        # 0-based
    start_ts_ms: int
    end_ts_ms: int
    bars: int
    trades: int
    win_rate: float
    expectancy: float
    profit_factor: float   # may be float('inf') if no losing trades; treat as None
    sharpe: float
    sortino: float
    max_drawdown: float    # 0..1
    total_return_pct: float


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    symbol: str
    timeframe: str
    strategy_label: str
    n_folds: int
    fold_size_bars: int
    folds: list[FoldResult]
    folds_pf_above_1: int          # count of folds with PF > 1.0 (excluding inf)
    folds_pf_below_05: int          # count of folds with PF < 0.5 (catastrophic)
    median_pf: float
    median_sharpe: float
    aggregate_return_pct: float    # compounded across folds
    verdict: str                   # "PASS" | "WEAK" | "FAIL"


def walk_forward(
    df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    strategy_label: str,
    strategy_fn: StrategyFn,
    config: BacktestConfig,
    n_folds: int = 6,
    min_bars_per_fold: int = 100,
) -> WalkForwardReport:
    """Run a strategy on N consecutive folds. Returns per-fold + aggregate metrics.

    Each fold is a fresh, independent backtest — the strategy resets equity/position
    at the start of each fold, so a single bad fold can't swamp others' contribution.
    """
    if df.empty or len(df) < n_folds * min_bars_per_fold:
        # Not enough data — fall back to fewer folds rather than erroring.
        n_folds = max(1, len(df) // min_bars_per_fold)
    fold_size = len(df) // n_folds

    folds: list[FoldResult] = []
    for i in range(n_folds):
        start = i * fold_size
        end = (i + 1) * fold_size if i < n_folds - 1 else len(df)
        fold_df = df.iloc[start:end].reset_index(drop=True)
        if len(fold_df) < min_bars_per_fold:
            continue

        sigs = strategy_fn(fold_df, symbol=symbol)
        result = run_backtest(fold_df, sigs, config)
        m = result.metrics
        pf = float(m.get("profit_factor", 0.0))
        folds.append(FoldResult(
            fold_index=i,
            start_ts_ms=int(fold_df["timestamp_ms"].iloc[0]),
            end_ts_ms=int(fold_df["timestamp_ms"].iloc[-1]),
            bars=len(fold_df),
            trades=int(m.get("num_trades", 0)),
            win_rate=float(m.get("win_rate", 0.0)),
            expectancy=float(m.get("expectancy", 0.0)),
            profit_factor=pf,
            sharpe=float(m.get("sharpe", 0.0)),
            sortino=float(m.get("sortino", 0.0)),
            max_drawdown=float(m.get("max_drawdown", 0.0)),
            total_return_pct=float(m.get("total_return_pct", 0.0)) * 100,
        ))

    # Aggregate stats — exclude infinite PFs (degenerate case: 1 winning trade, 0 losers)
    finite_pfs = [f.profit_factor for f in folds if math.isfinite(f.profit_factor)]
    pfs_above_1 = sum(1 for p in finite_pfs if p > 1.0)
    pfs_below_05 = sum(1 for p in finite_pfs if p < 0.5)
    median_pf = (sorted(finite_pfs)[len(finite_pfs) // 2] if finite_pfs else 0.0)
    finite_sharpes = [f.sharpe for f in folds if math.isfinite(f.sharpe)]
    median_sharpe = (sorted(finite_sharpes)[len(finite_sharpes) // 2] if finite_sharpes else 0.0)

    # Compounded return — what an operator would actually have ended with if rolling
    # equity forward through the folds (treats each fold's return as a multiplier).
    cumulative = 1.0
    for f in folds:
        cumulative *= (1.0 + f.total_return_pct / 100.0)
    aggregate_return_pct = (cumulative - 1.0) * 100.0

    # Verdict
    pass_threshold = math.ceil(len(folds) * 0.66)
    weak_threshold = math.ceil(len(folds) * 0.5)
    if pfs_above_1 >= pass_threshold and pfs_below_05 == 0:
        verdict = "PASS"
    elif pfs_above_1 >= weak_threshold:
        verdict = "WEAK"
    else:
        verdict = "FAIL"

    return WalkForwardReport(
        symbol=symbol,
        timeframe=timeframe,
        strategy_label=strategy_label,
        n_folds=len(folds),
        fold_size_bars=fold_size,
        folds=folds,
        folds_pf_above_1=pfs_above_1,
        folds_pf_below_05=pfs_below_05,
        median_pf=median_pf,
        median_sharpe=median_sharpe,
        aggregate_return_pct=aggregate_return_pct,
        verdict=verdict,
    )
