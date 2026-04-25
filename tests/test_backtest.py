"""Tests for backtest.engine + backtest.metrics."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.engine import BacktestConfig, run_backtest
from backtest.metrics import (
    break_even_win_rate,
    equity_returns,
    max_drawdown,
    sharpe,
    win_rate,
)
from data.binance import Bar
from features.compute import bars_to_df
from signals.types import Signal

HOUR_MS = 3_600_000


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    bars = [
        Bar(timestamp_ms=i * HOUR_MS, open=o, high=h, low=low, close=c, volume=1.0)
        for i, (o, h, low, c) in enumerate(rows)
    ]
    return bars_to_df(bars)


def _hold(ts: int) -> Signal:
    return Signal(ts, "BTC/USDT", "hold", 0.0, None, None, "")


def _buy(ts: int, stop: float, target: float) -> Signal:
    return Signal(ts, "BTC/USDT", "buy", 0.5, stop, target, "test")


def _sell(ts: int) -> Signal:
    return Signal(ts, "BTC/USDT", "sell", 0.0, None, None, "test")


# --- engine ---


def test_expected_buy_then_target_hit_records_profitable_trade() -> None:
    """Bar 0: buy signal. Bar 1: open=100, then high reaches target=110 → exit at target."""
    df = _bars([(100, 100, 100, 100), (100, 110, 99, 105), (105, 106, 104, 105)])
    sigs = [_buy(0, stop=95.0, target=110.0), _hold(HOUR_MS), _hold(2 * HOUR_MS)]

    res = run_backtest(df, sigs, BacktestConfig(commission_bps=0.0, slippage_bps=0.0))

    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.exit_reason == "target"
    assert t.exit_price == pytest.approx(110.0)
    assert t.pnl > 0


def test_edge_stop_takes_precedence_over_target_same_bar() -> None:
    """Both stop and target inside the same bar → assume stop hits first (conservative)."""
    df = _bars([(100, 100, 100, 100), (100, 120, 80, 100)])
    sigs = [_buy(0, stop=90.0, target=110.0), _hold(HOUR_MS)]

    res = run_backtest(df, sigs, BacktestConfig(commission_bps=0.0, slippage_bps=0.0))
    assert res.trades[0].exit_reason == "stop"
    assert res.trades[0].pnl < 0


def test_edge_open_position_at_end_closes_at_last_close() -> None:
    df = _bars([(100, 100, 100, 100), (100, 102, 99, 101), (101, 103, 100, 102)])
    sigs = [_buy(0, stop=95.0, target=200.0), _hold(HOUR_MS), _hold(2 * HOUR_MS)]

    res = run_backtest(df, sigs, BacktestConfig(commission_bps=0.0, slippage_bps=0.0))
    assert res.trades[0].exit_reason == "end_of_data"


def test_failure_signal_count_mismatch_raises() -> None:
    df = _bars([(100, 100, 100, 100), (100, 100, 100, 100)])
    with pytest.raises(ValueError, match="signals"):
        run_backtest(df, [_hold(0)])


def test_costs_drag_pnl() -> None:
    """With commissions/slippage, the same target-hit trade nets less than the no-cost case."""
    df = _bars([(100, 100, 100, 100), (100, 110, 99, 105)])
    sigs = [_buy(0, stop=95.0, target=110.0), _hold(HOUR_MS)]

    free = run_backtest(df, sigs, BacktestConfig(commission_bps=0.0, slippage_bps=0.0))
    real = run_backtest(df, sigs, BacktestConfig(commission_bps=10.0, slippage_bps=5.0))

    assert real.trades[0].pnl < free.trades[0].pnl


# --- metrics ---


def test_max_drawdown_known_curve() -> None:
    eq = pd.Series([100.0, 120.0, 90.0, 95.0, 130.0])
    assert max_drawdown(eq) == pytest.approx(0.25)  # 120 → 90 = 25%


def test_win_rate_basic() -> None:
    assert win_rate([1.0, -1.0, 2.0, -3.0]) == 0.5
    assert win_rate([]) == 0.0


def test_break_even_wr_2to1() -> None:
    assert break_even_win_rate(2.0) == pytest.approx(1 / 3)


def test_sharpe_zero_var_returns_zero() -> None:
    assert sharpe(pd.Series([0.01, 0.01, 0.01])) == 0.0


def test_equity_returns_log_diff() -> None:
    eq = pd.Series([100.0, 110.0, 121.0])
    r = equity_returns(eq)
    assert len(r) == 2
    assert r.iloc[0] == pytest.approx(r.iloc[1])  # constant 10% gain → equal log returns
