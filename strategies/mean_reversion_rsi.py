"""RSI mean-reversion strategy — long-only, ATR-based stops.

Counterpart to the baseline EMA-cross. Where EMA-cross is *trend following*
(buy strength, sell weakness), this is *mean reversion* (buy oversold extremes,
exit when price snaps back to the mean). The two have opposite edges across
regimes — trend-following loses in choppy markets, mean-reversion loses in
trending ones — so having both lets the dashboard show which regime we're in.

Long-only so it stays compatible with the spot/PaperBroker pipeline. Entry on
RSI crossing up *out of* oversold (e.g., from 28 → 32 with threshold 30) so we
catch the reversal, not the falling knife. Stop is ATR-based (same risk model
as baseline). Exit signal when RSI crosses up out of the neutral zone into
overbought territory (mean reverted) — the engine handles stop/target as well.
"""

from __future__ import annotations

import pandas as pd

from features.compute import atr, rsi
from signals.types import Signal


def generate_signals(
    df: pd.DataFrame,
    *,
    symbol: str = "BTC/USDT",
    rsi_period: int = 14,
    oversold: float = 30.0,
    overbought: float = 70.0,
    atr_period: int = 14,
    stop_atr_mult: float = 2.0,
    target_atr_mult: float = 3.0,
) -> list[Signal]:
    """Long-only RSI mean-reversion.

    - Buy when RSI crosses *up* through `oversold` (price was washed out, now
      reversing). Stop = close - k*ATR; target = close + 1.5k*ATR (lower R/R
      than trend-follower since reversals are quick and shallow).
    - Sell (exit) when RSI crosses *up* through `overbought` (mean has
      reverted past fair value).
    - Otherwise: hold.

    Causal: every value at row i depends only on rows <= i (P-05).
    """
    if not (0 < oversold < overbought < 100):
        raise ValueError(f"need 0 < oversold ({oversold}) < overbought ({overbought}) < 100")
    if stop_atr_mult <= 0 or target_atr_mult <= 0:
        raise ValueError("ATR multipliers must be > 0")
    if df.empty:
        return []

    r = rsi(df["close"], period=rsi_period)
    a = atr(df, period=atr_period)
    r_prev = r.shift(1)

    cross_up_oversold = (r > oversold) & (r_prev <= oversold)
    cross_up_overbought = (r > overbought) & (r_prev <= overbought)

    out: list[Signal] = []
    for i in range(len(df)):
        ts = int(df["timestamp_ms"].iloc[i])
        close = float(df["close"].iloc[i])
        atr_i = float(a.iloc[i]) if pd.notna(a.iloc[i]) else 0.0
        rsi_i = float(r.iloc[i]) if pd.notna(r.iloc[i]) else 50.0

        if cross_up_oversold.iloc[i] and atr_i > 0:
            stop = close - stop_atr_mult * atr_i
            target = close + target_atr_mult * atr_i
            # Conviction: how *deep* the oversold dip was — RSI prev value
            # below oversold means more washed out → higher conviction.
            depth = max(0.0, oversold - float(r_prev.iloc[i] or oversold))
            conv = min(1.0, depth / oversold)
            out.append(
                Signal(
                    timestamp_ms=ts,
                    symbol=symbol,
                    side="buy",
                    conviction=float(conv),
                    stop=float(stop),
                    target=float(target),
                    rationale=f"rsi cross-up from oversold ({rsi_i:.1f}); atr={atr_i:.2f}",
                )
            )
        elif cross_up_overbought.iloc[i]:
            out.append(
                Signal(
                    timestamp_ms=ts,
                    symbol=symbol,
                    side="sell",
                    conviction=0.0,
                    stop=None,
                    target=None,
                    rationale=f"rsi cross-up into overbought ({rsi_i:.1f})",
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
