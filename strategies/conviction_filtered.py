"""Conviction-threshold-filtered strategy wrapper — deterministic backtest stand-in
for the live LLM filter.

The live bot, when TRADERBOT_USE_LLM_FILTER=true, routes every BUY through
Claude. That's expensive in backtest (one API call per signal × 30 days × N
symbols × multiple comparisons), so this gives us a *deterministic* analog:
filter buys by the strategy's own conviction score against a threshold.

When the conviction-filtered backtest improves over the raw strategy, that's
strong evidence the LLM filter (which uses richer context — RSI, ATR, R/R
ratio — than just conviction) will improve it at least as much. When it
*doesn't* improve, the strategy is signaling at uniform conviction and a
filter probably won't help — pick a different strategy.

Same `(df, *, symbol) → list[Signal]` shape as every other strategy, so it
plugs into compare/expectancy panels the same way.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from signals.types import Signal


def make_conviction_filtered(
    base_fn,
    *,
    threshold: float = 0.5,
):
    """Return a strategy that drops buys with conviction < threshold."""

    def wrapped(df: pd.DataFrame, *, symbol: str = "BTC/USDT") -> list[Signal]:
        base = base_fn(df, symbol=symbol)
        out: list[Signal] = []
        for sig in base:
            if sig.side != "buy":
                out.append(sig)
                continue
            if sig.conviction >= threshold:
                out.append(replace(sig, rationale=f"{sig.rationale} | conv≥{threshold:.2f}"))
            else:
                out.append(
                    Signal(
                        timestamp_ms=sig.timestamp_ms,
                        symbol=sig.symbol,
                        side="hold",
                        conviction=0.0,
                        stop=None,
                        target=None,
                        rationale=f"filtered: conv {sig.conviction:.2f} < {threshold:.2f}",
                    )
                )
        return out

    wrapped.__name__ = f"conviction_filtered({getattr(base_fn, '__name__', 'base')})"
    return wrapped
