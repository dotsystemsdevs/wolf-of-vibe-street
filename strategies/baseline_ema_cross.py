"""Baseline EMA-cross strategy — long-only, ATR-based stops.

This is intentionally trivial. Its job is to prove the pipe end-to-end (data → features
→ signal → risk → backtest). No LLM, no ML. Replace once the pipe is verified.
"""

from __future__ import annotations

import pandas as pd

from features.compute import atr, ema
from signals.types import Signal


def generate_signals(
    df: pd.DataFrame,
    *,
    symbol: str = "BTC/USDT",
    fast: int = 12,
    slow: int = 26,
    atr_period: int = 14,
    stop_atr_mult: float = 2.0,
    target_atr_mult: float = 4.0,
) -> list[Signal]:
    """Long-only EMA crossover.

    - Bullish cross (fast crosses *above* slow): emit `buy` with stop = close - k*ATR
      and target = close + 2k*ATR (default 2 R/R).
    - Bearish cross (fast crosses *below* slow): emit `sell` to exit.
    - Otherwise: emit `hold`.

    Causal: every value at row i is derived from rows <= i (P-05).
    """
    if fast >= slow:
        raise ValueError(f"fast ({fast}) must be < slow ({slow})")
    if stop_atr_mult <= 0 or target_atr_mult <= 0:
        raise ValueError("ATR multipliers must be > 0")
    if df.empty:
        return []

    fast_ema = ema(df["close"], span=fast)
    slow_ema = ema(df["close"], span=slow)
    a = atr(df, period=atr_period)

    diff = fast_ema - slow_ema
    diff_prev = diff.shift(1)
    cross_up = (diff > 0) & (diff_prev <= 0)
    cross_dn = (diff < 0) & (diff_prev >= 0)

    out: list[Signal] = []
    for i in range(len(df)):
        ts = int(df["timestamp_ms"].iloc[i])
        close = float(df["close"].iloc[i])
        atr_i = float(a.iloc[i]) if pd.notna(a.iloc[i]) else 0.0

        if cross_up.iloc[i] and atr_i > 0:
            stop = close - stop_atr_mult * atr_i
            target = close + target_atr_mult * atr_i
            conv = min(1.0, abs(diff.iloc[i]) / close * 100.0)
            out.append(
                Signal(
                    timestamp_ms=ts,
                    symbol=symbol,
                    side="buy",
                    conviction=float(conv),
                    stop=float(stop),
                    target=float(target),
                    rationale=f"ema{fast}>ema{slow} cross-up; atr={atr_i:.2f}",
                )
            )
        elif cross_dn.iloc[i]:
            out.append(
                Signal(
                    timestamp_ms=ts,
                    symbol=symbol,
                    side="sell",
                    conviction=0.0,
                    stop=None,
                    target=None,
                    rationale=f"ema{fast}<ema{slow} cross-down",
                )
            )
        else:
            out.append(
                Signal(
                    timestamp_ms=ts,
                    symbol=symbol,
                    side="hold",
                    conviction=0.0,
                    stop=None,
                    target=None,
                    rationale="",
                )
            )
    return out
