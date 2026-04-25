"""Tests for features.compute — happy/edge/failure per feature, plus a P-05 lookahead guard."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from data.binance import Bar
from features.compute import atr, bars_to_df, ema, returns, rsi, volatility_regime

HOUR_MS = 3_600_000


def _bar(ts: int, o: float, h: float, low: float, c: float, v: float = 1.0) -> Bar:
    return Bar(timestamp_ms=ts, open=o, high=h, low=low, close=c, volume=v)


def _toy_df(closes: list[float]) -> pd.DataFrame:
    """Build a minimal valid OHLCV DataFrame from a close-price series."""
    bars = [_bar(i * HOUR_MS, c, c + 1, c - 1, c) for i, c in enumerate(closes)]
    return bars_to_df(bars)


# --- bars_to_df ----------------------------------------------------------------------


def test_bars_to_df_sorts_and_deduplicates_check() -> None:
    """Happy: out-of-order input is sorted; final order is monotonic."""
    bars = [_bar(2 * HOUR_MS, 2, 3, 1, 2), _bar(0, 1, 2, 0, 1), _bar(HOUR_MS, 1.5, 2, 1, 1.5)]
    df = bars_to_df(bars)
    assert list(df["timestamp_ms"]) == [0, HOUR_MS, 2 * HOUR_MS]


def test_bars_to_df_empty_returns_empty_df() -> None:
    df = bars_to_df([])
    assert df.empty
    assert "close" in df.columns


def test_bars_to_df_rejects_duplicate_timestamps() -> None:
    bars = [_bar(0, 1, 2, 0, 1), _bar(0, 1.1, 2, 0, 1.1)]
    with pytest.raises(ValueError, match="duplicate"):
        bars_to_df(bars)


# --- returns -------------------------------------------------------------------------


def test_returns_expected_values() -> None:
    s = pd.Series([100.0, 110.0, 99.0])
    r = returns(s)
    assert math.isnan(r.iloc[0])
    assert r.iloc[1] == pytest.approx(0.1)
    assert r.iloc[2] == pytest.approx(-0.1)


# --- ema -----------------------------------------------------------------------------


def test_ema_known_value() -> None:
    """EMA(span=2) of [1,2,3,4]: alpha=2/3.
    e0=1, e1=2/3*2 + 1/3*1 = 5/3, e2=2/3*3 + 1/3*5/3 = 23/9, e3=2/3*4 + 1/3*23/9 = 95/27."""
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    out = ema(s, span=2)
    assert out.iloc[0] == pytest.approx(1.0)
    assert out.iloc[1] == pytest.approx(5 / 3)
    assert out.iloc[2] == pytest.approx(23 / 9)
    assert out.iloc[3] == pytest.approx(95 / 27)


def test_ema_constant_input_returns_constant() -> None:
    """Edge: a flat series has ema == value at every point."""
    s = pd.Series([42.0] * 10)
    assert (ema(s, span=5) == 42.0).all()


def test_ema_invalid_span_raises() -> None:
    with pytest.raises(ValueError):
        ema(pd.Series([1.0]), span=0)


# --- rsi -----------------------------------------------------------------------------


def test_rsi_strictly_rising_approaches_100() -> None:
    """Happy: monotonic up → RSI converges toward 100."""
    s = pd.Series([float(x) for x in range(1, 50)])
    out = rsi(s, period=14)
    assert out.iloc[-1] > 99.0


def test_rsi_strictly_falling_approaches_0() -> None:
    """Edge: monotonic down → RSI converges toward 0."""
    s = pd.Series([float(x) for x in range(50, 1, -1)])
    out = rsi(s, period=14)
    assert out.iloc[-1] < 1.0


def test_rsi_invalid_period_raises() -> None:
    with pytest.raises(ValueError):
        rsi(pd.Series([1.0, 2.0]), period=1)


# --- atr -----------------------------------------------------------------------------


def test_atr_constant_bars_zero() -> None:
    """Edge: high==low==close on every bar → TR=0 → ATR=0."""
    df = _toy_df([100.0] * 20)
    df["high"] = 100.0
    df["low"] = 100.0
    out = atr(df, period=5)
    assert out.iloc[-1] == pytest.approx(0.0)


def test_atr_known_simple_case() -> None:
    """Happy: ranges 2,2,2 → ATR converges to 2."""
    df = _toy_df([100.0, 100.0, 100.0, 100.0, 100.0])  # close flat
    df["high"] = 101.0
    df["low"] = 99.0
    out = atr(df, period=3)
    assert out.iloc[-1] == pytest.approx(2.0)


def test_atr_missing_column_raises() -> None:
    df = pd.DataFrame({"close": [1.0, 2.0]})
    with pytest.raises(ValueError, match="atr requires column"):
        atr(df)


# --- volatility_regime ---------------------------------------------------------------


def test_volatility_regime_labels_three_buckets() -> None:
    """Happy: 100 flat bars then 50 wide ones → tail labels = 'high'."""
    flat = [100.0] * 100
    wide = [100.0] * 50
    df = _toy_df(flat + wide)
    df.loc[100:, "high"] = 110.0
    df.loc[100:, "low"] = 90.0
    out = volatility_regime(df, atr_period=14, lookback=50)
    assert out.iloc[-1] == "high"


def test_volatility_regime_invalid_lookback_raises() -> None:
    df = _toy_df([100.0, 101.0, 102.0])
    with pytest.raises(ValueError):
        volatility_regime(df, lookback=2)


# --- the lookahead guard (P-05) ------------------------------------------------------


@pytest.mark.parametrize(
    "fn",
    [
        lambda df: ema(df["close"], span=5),
        lambda df: rsi(df["close"], period=7),
        lambda df: atr(df, period=7),
        lambda df: returns(df["close"]),
    ],
)
def test_no_lookahead_modifying_future_does_not_change_past(fn) -> None:
    """P-05 guard: changing data at index >= k must not change the feature at index < k.

    If a feature ever uses `.shift(-1)` or any future-touching op by mistake, this
    test will catch it for every feature wired through the parametrize list.
    """
    closes = [100.0 + i * 0.5 for i in range(60)]
    df = _toy_df(closes)
    original = fn(df)

    df_modified = df.copy()
    cutoff = 40
    df_modified.loc[cutoff:, "close"] = 9999.0
    df_modified.loc[cutoff:, "high"] = 9999.0
    df_modified.loc[cutoff:, "low"] = 9999.0
    perturbed = fn(df_modified)

    pd.testing.assert_series_equal(
        original.iloc[:cutoff], perturbed.iloc[:cutoff], check_names=False
    )
