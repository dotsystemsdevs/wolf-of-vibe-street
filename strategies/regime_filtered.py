"""Regime filter — wraps any base strategy and blocks BUYs when the market is in a downtrend.

The classic trend-following problem: strategies that work in uptrends bleed during
bear regimes. EMA-cross specifically gets whipsawed when price ranges around the
slow EMA. The fix: an EMA200 (default) regime filter — only allow BUY signals
when close > EMA200, i.e. price is above the long-term trend line.

SELL signals always pass through (you must always be allowed to exit).
HOLD signals always pass through.

Reasoning:
- During bear (close < EMA200), bot stays in cash. No BUY signals fired.
- During uptrend (close > EMA200), base strategy operates as normal.
- Reduces trade count but improves expectancy IF the base strategy is mean-positive
  in uptrend conditions.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from features.compute import ema
from signals.types import Signal

StrategyFn = Callable[..., list[Signal]]


def make_regime_filtered(
    base_fn: StrategyFn,
    *,
    regime_period: int = 200,
) -> StrategyFn:
    """Returns a new strategy fn that only emits BUYs when close > EMA(`regime_period`).

    Sells/holds pass through unchanged so the bot can always exit. The wrapper
    is causal: at row i we only look at EMA200(t<=i), never future bars.
    """

    def filtered(df: pd.DataFrame, *, symbol: str = "BTC/USDT", **kwargs) -> list[Signal]:
        signals = base_fn(df, symbol=symbol, **kwargs)
        if df.empty or not signals:
            return signals

        regime_ema = ema(df["close"], span=regime_period)
        # Index signals by timestamp for O(1) lookup of corresponding bar.
        ts_to_close: dict[int, float] = dict(zip(
            df["timestamp_ms"].astype(int).tolist(),
            df["close"].astype(float).tolist(),
            strict=False,
        ))
        ts_to_regime: dict[int, float] = dict(zip(
            df["timestamp_ms"].astype(int).tolist(),
            regime_ema.tolist(),
            strict=False,
        ))

        out: list[Signal] = []
        for s in signals:
            if s.side != "buy":
                out.append(s)
                continue
            close_i = ts_to_close.get(s.timestamp_ms)
            regime_i = ts_to_regime.get(s.timestamp_ms)
            if close_i is None or regime_i is None or pd.isna(regime_i):
                # Not enough history for EMA — block buy to be safe.
                out.append(Signal(
                    timestamp_ms=s.timestamp_ms,
                    symbol=s.symbol,
                    side="hold",
                    conviction=0.0,
                    stop=None,
                    target=None,
                    rationale=f"regime: insufficient history (ema{regime_period} undefined)",
                ))
            elif close_i < regime_i:
                # Bearish regime — block the buy.
                out.append(Signal(
                    timestamp_ms=s.timestamp_ms,
                    symbol=s.symbol,
                    side="hold",
                    conviction=0.0,
                    stop=None,
                    target=None,
                    rationale=f"regime block: close={close_i:.2f} < ema{regime_period}={regime_i:.2f}",
                ))
            else:
                # Uptrend confirmed — pass the buy through.
                out.append(Signal(
                    timestamp_ms=s.timestamp_ms,
                    symbol=s.symbol,
                    side=s.side,
                    conviction=s.conviction,
                    stop=s.stop,
                    target=s.target,
                    rationale=f"{s.rationale} | regime ok: close>ema{regime_period}",
                ))
        return out

    return filtered
