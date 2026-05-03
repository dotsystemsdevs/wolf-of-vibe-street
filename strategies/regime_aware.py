"""Regime-aware strategy selector.

Detects market regime per bar and routes each bar's signal decision to the
right sub-strategy:

  - uptrend   → trend-following (e.g. baseline EMA-cross)
  - sideways  → mean-reversion (e.g. mean_rev no stops)
  - downtrend → force hold (or use a defensive sub-strategy)

The position state is shared across sub-strategies — if uptrend strategy opened
a position and regime flips to sideways before exit, the SELL from any strategy
still passes through. The router only blocks NEW entries (BUYs).
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from features.regime import detect_regime
from signals.types import Signal

StrategyFn = Callable[..., list[Signal]]


def make_regime_aware(
    *,
    uptrend_fn: StrategyFn,
    sideways_fn: StrategyFn,
    downtrend_fn: StrategyFn | None = None,
    trend_period: int = 200,
) -> StrategyFn:
    """Returns a composite strategy that picks per-bar based on detected regime.

    Sells from any sub-strategy pass through (so positions can always exit).
    Buys only fire from the strategy matching the current regime.
    """

    def composite(df: pd.DataFrame, *, symbol: str = "BTC/USDT", **kwargs) -> list[Signal]:
        if df.empty:
            return []

        regimes = detect_regime(df, trend_period=trend_period)
        up_sigs = uptrend_fn(df, symbol=symbol)
        side_sigs = sideways_fn(df, symbol=symbol)
        down_sigs = downtrend_fn(df, symbol=symbol) if downtrend_fn else None

        out: list[Signal] = []
        for i in range(len(df)):
            ts = int(df["timestamp_ms"].iloc[i])
            trend = regimes["trend"].iloc[i]
            # Pick the per-regime signal for this bar
            if trend == "uptrend":
                pick = up_sigs[i] if i < len(up_sigs) else None
                tag = "uptrend"
            elif trend == "sideways":
                pick = side_sigs[i] if i < len(side_sigs) else None
                tag = "sideways"
            else:  # downtrend
                pick = down_sigs[i] if down_sigs and i < len(down_sigs) else None
                tag = "downtrend"

            # Always allow exits (sell for longs, cover for shorts) from ANY
            # sub-strategy — protects open positions when regime flips against us.
            exits: list[Signal] = []
            for sigs in (up_sigs, side_sigs):
                if i < len(sigs) and sigs[i].side in ("sell", "cover"):
                    exits.append(sigs[i])
            if down_sigs and i < len(down_sigs) and down_sigs[i].side in ("sell", "cover"):
                exits.append(down_sigs[i])

            if exits:
                ex = exits[0]
                out.append(Signal(
                    timestamp_ms=ts, symbol=symbol, side=ex.side,
                    conviction=0.0, stop=None, target=None,
                    rationale=f"[regime: {trend}] {ex.rationale}",
                ))
                continue

            if pick is None:
                out.append(Signal(
                    timestamp_ms=ts, symbol=symbol, side="hold",
                    conviction=0.0, stop=None, target=None,
                    rationale=f"[regime: {trend}] no active strategy",
                ))
                continue

            # Forward entries from the regime-matched sub-strategy. Both buy and
            # short are entries with stops; everything else passes through as hold.
            if pick.side in ("buy", "short"):
                out.append(Signal(
                    timestamp_ms=ts, symbol=symbol, side=pick.side,
                    conviction=pick.conviction, stop=pick.stop, target=pick.target,
                    rationale=f"[regime: {tag}] {pick.rationale}",
                ))
            else:
                out.append(Signal(
                    timestamp_ms=ts, symbol=symbol, side="hold",
                    conviction=0.0, stop=None, target=None,
                    rationale=f"[regime: {trend}] hold",
                ))

        return out

    return composite
