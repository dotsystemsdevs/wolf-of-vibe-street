"""Short-side mean-reversion (added 2026-05-03 for bull+bear capability).

Mirror image of the long mean-rev strategy:
  - Long mean-rev: RSI crosses UP from oversold → buy bounce, exit when overbought
  - Short mean-rev: RSI crosses DOWN from overbought → short the rejection,
    exit when oversold (price has reverted down enough)

Same "no hard stops" thinking — mean-reversion needs room to play out, ATR stops
trigger on noise. The position is exited on signal (RSI back to neutral) or by
risk caps if it really runs against us.

Used inside `regime_aware_long_short` to take advantage of bear regimes that
the long-only `regime_aware_dipbuy` couldn't trade. Spot-only paper synthesizes
shorts; in live this requires a perp-futures broker (Binance Futures, Kraken
Futures, etc.) — same Signal contract, different broker adapter.
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
    atr_period: int = 14,
    overbought: float = 70.0,
    oversold: float = 30.0,
    stop_atr_mult: float = 20.0,   # effectively disabled — let strategy manage
    target_atr_mult: float = 20.0,
) -> list[Signal]:
    """Short on RSI cross-down through `overbought`; cover on cross-down through `oversold`.

    Stop is set ABOVE entry (where the asset would have to rally back to to
    invalidate the short thesis). With stop_atr_mult=20 the hard stop is far
    enough away that signal-based exit dominates — same pattern as
    make_no_stops_mean_rev for longs.
    """
    if df.empty or len(df) < max(rsi_period, atr_period) + 2:
        return [
            Signal(int(t), symbol, "hold", 0.0, None, None, "")
            for t in df["timestamp_ms"].tolist()
        ]

    close = df["close"]
    r = rsi(close, period=rsi_period)
    atr_s = atr(df, period=atr_period)

    r_prev = r.shift(1)
    cross_down_overbought = (r < overbought) & (r_prev >= overbought)
    cross_down_oversold = (r < oversold) & (r_prev >= oversold)

    out: list[Signal] = []
    for i in range(len(df)):
        ts = int(df["timestamp_ms"].iloc[i])
        rsi_i = r.iloc[i]
        atr_i = atr_s.iloc[i] if i < len(atr_s) else None

        if pd.isna(rsi_i) or atr_i is None or pd.isna(atr_i):
            out.append(Signal(ts, symbol, "hold", 0.0, None, None, ""))
            continue

        if cross_down_overbought.iloc[i] and atr_i > 0:
            entry = float(close.iloc[i])
            # Stop ABOVE entry; target BELOW entry. Conviction scales with how
            # deeply we cracked overbought (higher RSI prev → stronger fade).
            stop = entry + atr_i * stop_atr_mult
            target = entry - atr_i * target_atr_mult
            depth = max(0.0, float(r_prev.iloc[i] or overbought) - overbought)
            conv = min(1.0, depth / (100 - overbought))
            out.append(
                Signal(
                    timestamp_ms=ts,
                    symbol=symbol,
                    side="short",
                    conviction=conv,
                    stop=stop,
                    target=target,
                    rationale=(
                        f"rsi cross-down from overbought ({rsi_i:.1f}); "
                        f"atr={atr_i:.2f}"
                    ),
                )
            )
        elif cross_down_oversold.iloc[i]:
            out.append(
                Signal(
                    timestamp_ms=ts,
                    symbol=symbol,
                    side="cover",
                    conviction=0.0,
                    stop=None,
                    target=None,
                    rationale=f"rsi cross-down into oversold ({rsi_i:.1f})",
                )
            )
        else:
            out.append(Signal(ts, symbol, "hold", 0.0, None, None, ""))

    return out


def make_short_mean_rev():
    """Factory matching the make_no_stops_mean_rev convention. Disables
    hard stops via large ATR multipliers so signal-based cover dominates."""

    def fn(df, **kw):
        kw.pop("stop_atr_mult", None)
        kw.pop("target_atr_mult", None)
        return generate_signals(df, stop_atr_mult=20.0, target_atr_mult=20.0, **kw)

    return fn
