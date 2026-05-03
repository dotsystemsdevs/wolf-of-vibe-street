"""Momentum-breakout strategy (Donchian channel).

Classic trend-following: BUY when price closes above the highest high of the last
`entry_lookback` bars (default 20 = ~20h on 1h-bars). SELL when price closes below
the lowest low of the last `exit_lookback` bars (default 10).

The asymmetric lookback (entry > exit) creates room for the trend to develop without
prematurely exiting on noise. Famous from the original "turtle traders" system,
but tuned shorter here for crypto's higher volatility.

Long-only, ATR-based stop (same convention as baseline_ema_cross). Causal — at
row i we only look at rows < i for the breakout reference.
"""

from __future__ import annotations

import pandas as pd

from features.compute import atr
from signals.types import Signal


def generate_signals(
    df: pd.DataFrame,
    *,
    symbol: str = "BTC/USDT",
    entry_lookback: int = 20,
    exit_lookback: int = 10,
    atr_period: int = 14,
    stop_atr_mult: float = 2.0,
    target_atr_mult: float = 4.0,
) -> list[Signal]:
    """Donchian-channel breakout. BUY on highest-high break, SELL on lowest-low break."""
    if entry_lookback < 2 or exit_lookback < 2:
        raise ValueError("lookbacks must be >= 2")
    if df.empty:
        return []

    # Causal high/low — exclude the current bar so signal fires only AFTER bar closes
    # above the prior window's extreme.
    rolling_high = df["high"].shift(1).rolling(window=entry_lookback, min_periods=entry_lookback).max()
    rolling_low = df["low"].shift(1).rolling(window=exit_lookback, min_periods=exit_lookback).min()
    a = atr(df, period=atr_period)

    # Track open position state to decide which signal to emit.
    in_position = False
    out: list[Signal] = []

    for i in range(len(df)):
        ts = int(df["timestamp_ms"].iloc[i])
        close = float(df["close"].iloc[i])
        hi = rolling_high.iloc[i]
        lo = rolling_low.iloc[i]
        atr_i = float(a.iloc[i]) if pd.notna(a.iloc[i]) else 0.0

        if not in_position and pd.notna(hi) and close > float(hi) and atr_i > 0:
            stop = close - stop_atr_mult * atr_i
            target = close + target_atr_mult * atr_i
            # Conviction = how far above breakout level (normalized by ATR)
            conv = min(1.0, max(0.0, (close - float(hi)) / atr_i))
            out.append(Signal(
                timestamp_ms=ts,
                symbol=symbol,
                side="buy",
                conviction=float(conv),
                stop=float(stop),
                target=float(target),
                rationale=f"breakout: close={close:.2f} > {entry_lookback}-bar high={float(hi):.2f}; atr={atr_i:.2f}",
            ))
            in_position = True
        elif in_position and pd.notna(lo) and close < float(lo):
            out.append(Signal(
                timestamp_ms=ts,
                symbol=symbol,
                side="sell",
                conviction=0.0,
                stop=None,
                target=None,
                rationale=f"breakdown: close={close:.2f} < {exit_lookback}-bar low={float(lo):.2f}",
            ))
            in_position = False
        else:
            out.append(Signal(
                timestamp_ms=ts,
                symbol=symbol,
                side="hold",
                conviction=0.0,
                stop=None,
                target=None,
                rationale="",
            ))

    return out
