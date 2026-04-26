"""Tests for strategies.mean_reversion_rsi.

3-test rule (CLAUDE.md §3.1):
  - expected: oversold dip → bounce → buy fires
  - edge: empty df + flat prices
  - failure: invalid params raise
"""

from __future__ import annotations

import pandas as pd
import pytest

from data.binance import Bar
from features.compute import bars_to_df
from strategies.mean_reversion_rsi import generate_signals

HOUR_MS = 3_600_000


def _bars(closes: list[float]) -> pd.DataFrame:
    bars = [
        Bar(timestamp_ms=i * HOUR_MS, open=c, high=c + 1, low=c - 1, close=c, volume=1.0)
        for i, c in enumerate(closes)
    ]
    return bars_to_df(bars)


def test_expected_buy_on_oversold_bounce_with_valid_stop_and_target() -> None:
    """Sustained drop drives RSI deep into oversold; the bounce must fire ≥1 buy."""
    # 30 flat bars to seed the EWM, then a long monotone drop, then a clean bounce.
    closes = (
        [100.0] * 30
        + [100.0 - i for i in range(1, 25)]  # steady drop → RSI < 30
        + [76.0 + i for i in range(1, 10)]  # bounce → RSI crosses back up
    )
    df = _bars(closes)
    sigs = generate_signals(df, rsi_period=14, oversold=30.0, atr_period=5)
    buys = [s for s in sigs if s.side == "buy"]
    assert len(buys) >= 1
    first = buys[0]
    assert first.stop is not None and first.target is not None
    assert first.stop < first.target  # long, so stop below target
    assert "rsi cross-up from oversold" in first.rationale


def test_edge_empty_and_flat_prices_emit_no_buys() -> None:
    assert generate_signals(_bars([])) == []
    flat_sigs = generate_signals(_bars([100.0] * 50))
    assert all(s.side == "hold" for s in flat_sigs)
    assert len(flat_sigs) == 50


def test_failure_invalid_thresholds_raise() -> None:
    df = _bars([100.0] * 30)
    with pytest.raises(ValueError, match="oversold"):
        generate_signals(df, oversold=70.0, overbought=30.0)  # inverted
    with pytest.raises(ValueError, match="ATR multipliers"):
        generate_signals(df, stop_atr_mult=0.0)
    with pytest.raises(ValueError, match="ATR multipliers"):
        generate_signals(df, target_atr_mult=-1.0)
