"""Feature computations — single source of truth for train, backtest, and live (I-2).

Every function here MUST be causal (P-05): the value at time t may only use data
indexed <= t. Tests in `tests/test_compute.py` enforce this with a lookahead guard.
"""

from __future__ import annotations

import pandas as pd

from data.binance import Bar

REQUIRED_COLUMNS = ("timestamp_ms", "open", "high", "low", "close", "volume")


def bars_to_df(bars: list[Bar]) -> pd.DataFrame:
    """Convert OHLCV bars to a DataFrame indexed by UTC datetime.

    Sorts ascending and asserts strictly-monotonic timestamps so downstream code
    can rely on row order.
    """
    if not bars:
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))
    df = pd.DataFrame(bars).sort_values("timestamp_ms").reset_index(drop=True)
    if not df["timestamp_ms"].is_monotonic_increasing:
        raise ValueError("bars contain non-monotonic timestamps after sort")
    if df["timestamp_ms"].duplicated().any():
        raise ValueError("bars contain duplicate timestamps")
    return df


def returns(close: pd.Series) -> pd.Series:
    """Simple period-over-period returns. First row is NaN (no prior close)."""
    return close.pct_change()


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average (adjust=False — matches trader convention).

    `span` is the standard EMA span: alpha = 2 / (span + 1).
    """
    if span < 1:
        raise ValueError(f"span must be >= 1, got {span}")
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI on `close`. Range [0, 100]. Returns NaN until `period` rows seen."""
    if period < 2:
        raise ValueError(f"period must be >= 2, got {period}")
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder smoothing). Requires high/low/close columns."""
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    for col in ("high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"atr requires column {col!r}")
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def volatility_regime(df: pd.DataFrame, atr_period: int = 14, lookback: int = 100) -> pd.Series:
    """Label each bar 'low' / 'med' / 'high' by trailing-window tercile of ATR/close.

    Uses the rolling percentile of `atr/close` over `lookback` bars (causal — only past
    + current data). NaN until `lookback` bars are available.
    """
    if lookback < 3:
        raise ValueError(f"lookback must be >= 3, got {lookback}")
    rel_vol = atr(df, period=atr_period) / df["close"]
    pct = rel_vol.rolling(lookback).rank(pct=True)
    return pd.cut(
        pct,
        bins=[-0.001, 1 / 3, 2 / 3, 1.001],
        labels=["low", "med", "high"],
    )
