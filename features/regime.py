"""Market regime detection — classifies each bar into trend + volatility cells.

Regimes drive conditional strategy selection: trend-following in uptrends,
mean-reversion in sideways markets, cash during downtrends.

Definitions (causal — only uses data ≤ bar i):
  Trend:
    - uptrend:   close > EMA200 AND EMA200 slope rising > +0.1% over 20 bars
    - downtrend: close < EMA200 AND EMA200 slope falling < -0.1% over 20 bars
    - sideways:  otherwise (oscillating, or weak trend)

  Volatility (optional, for sizing):
    - low_vol:  current ATR(14) below the median of trailing 100 bars
    - high_vol: at or above median

Output is a DataFrame with columns: trend, vol, regime (combined).
"""

from __future__ import annotations

import pandas as pd

from features.compute import atr, ema


def detect_regime(
    df: pd.DataFrame,
    *,
    trend_period: int = 200,
    slope_lookback: int = 20,
    slope_threshold: float = 0.001,
    atr_period: int = 14,
    vol_lookback: int = 100,
) -> pd.DataFrame:
    """Per-bar regime classification.

    Returns a DataFrame aligned with `df` with columns:
      - trend:  'uptrend' | 'downtrend' | 'sideways'
      - vol:    'low_vol' | 'high_vol'
      - regime: combined label (e.g. 'uptrend-low_vol')
    """
    ema_long = ema(df["close"], span=trend_period)
    slope = ema_long.pct_change(slope_lookback)
    a = atr(df, period=atr_period)
    vol_median = a.rolling(window=vol_lookback, min_periods=vol_lookback // 2).median()

    out = pd.DataFrame(index=df.index)
    out["trend"] = "sideways"
    above = df["close"] > ema_long
    below = df["close"] < ema_long
    rising = slope > slope_threshold
    falling = slope < -slope_threshold
    out.loc[above & rising, "trend"] = "uptrend"
    out.loc[below & falling, "trend"] = "downtrend"

    out["vol"] = "low_vol"
    out.loc[a >= vol_median, "vol"] = "high_vol"
    out["regime"] = out["trend"] + "-" + out["vol"]
    return out
