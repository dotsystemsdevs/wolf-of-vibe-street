"""Tests for strategies.baseline_ema_cross."""

from __future__ import annotations

import pandas as pd
import pytest

from data.binance import Bar
from features.compute import bars_to_df
from strategies.baseline_ema_cross import generate_signals

HOUR_MS = 3_600_000


def _bars(closes: list[float]) -> pd.DataFrame:
    bars = [
        Bar(timestamp_ms=i * HOUR_MS, open=c, high=c + 1, low=c - 1, close=c, volume=1.0)
        for i, c in enumerate(closes)
    ]
    return bars_to_df(bars)


def test_expected_buy_on_bullish_cross_then_hold() -> None:
    """Happy: prices flatten then rip → at least one buy signal with valid stop+target."""
    closes = [100.0] * 30 + [100.0 + i for i in range(1, 30)]
    df = _bars(closes)
    sigs = generate_signals(df, fast=3, slow=8, atr_period=5)

    buys = [s for s in sigs if s.side == "buy"]
    assert len(buys) >= 1
    first = buys[0]
    assert first.stop is not None and first.target is not None
    assert first.stop < first.target  # 2:1 R/R structure


def test_edge_empty_df_returns_empty() -> None:
    assert generate_signals(_bars([])) == []


def test_edge_flat_prices_yield_only_holds() -> None:
    """No crosses means no entries/exits."""
    sigs = generate_signals(_bars([100.0] * 100), fast=3, slow=8)
    assert all(s.side == "hold" for s in sigs)


def test_failure_fast_geq_slow_raises() -> None:
    with pytest.raises(ValueError, match="fast"):
        generate_signals(_bars([100.0] * 5), fast=10, slow=10)


def test_one_signal_per_bar() -> None:
    """Invariant: every bar gets exactly one signal (hold/buy/sell)."""
    df = _bars([100.0 + i * 0.1 for i in range(50)])
    sigs = generate_signals(df, fast=3, slow=8)
    assert len(sigs) == len(df)
