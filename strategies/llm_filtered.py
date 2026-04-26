"""LLM-filtered strategy wrapper (S-33: hybrid trigger + LLM evaluator).

Takes a base strategy's signals and routes every `buy` through an evaluator. Approved
buys pass through with the LLM rationale appended; rejected buys become `hold` with the
rejection reason recorded — both end up in the decision log so a post-mortem can see
exactly which trades the LLM killed and why.

`sell` and `hold` signals pass through unchanged. Per S-33, exits are not the LLM's job.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pandas as pd

from agents.llm_evaluator import LLMEvaluator, Verdict
from features.compute import atr, ema, rsi
from signals.types import Signal


def _build_context(df: pd.DataFrame, idx: int, signal: Signal) -> dict[str, Any]:
    """Snapshot of the bar's market state for the evaluator. Causal: uses rows <= idx."""
    window = df.iloc[: idx + 1]
    if len(window) < 2:
        return {"entry_price": float(window["close"].iloc[-1])}

    close = window["close"]
    fast = ema(close, span=12)
    slow = ema(close, span=26)
    rsi_series = rsi(close, period=14)
    atr_series = atr(window, period=14)

    entry = float(close.iloc[-1])
    ctx: dict[str, Any] = {
        "entry_price": round(entry, 2),
        "recent_closes": [round(float(x), 2) for x in close.iloc[-5:].tolist()],
        "ema_fast": round(float(fast.iloc[-1]), 2),
        "ema_slow": round(float(slow.iloc[-1]), 2),
        "rsi": round(float(rsi_series.iloc[-1]), 1) if pd.notna(rsi_series.iloc[-1]) else None,
        "atr": round(float(atr_series.iloc[-1]), 2) if pd.notna(atr_series.iloc[-1]) else None,
    }
    if signal.stop is not None:
        ctx["stop_pct"] = (signal.stop - entry) / entry
    if signal.target is not None:
        ctx["target_pct"] = (signal.target - entry) / entry
    if signal.stop is not None and signal.target is not None and entry > signal.stop:
        ctx["rr_ratio"] = (signal.target - entry) / (entry - signal.stop)
    return ctx


def make_llm_filtered_strategy(
    base_fn,
    evaluator: LLMEvaluator,
    *,
    threshold: float = 0.3,
):
    """Adapt llm_filtered_signals to the (df, *, symbol) → list[Signal] strategy shape.

    Used by workers.live_loop.build_from_env to wire the filter into the live loop
    when TRADERBOT_USE_LLM_FILTER=true. The base_fn is whatever strategy is
    currently selected (baseline / mean-reversion); the LLM only ever sees buys.
    """

    def wrapped(df: pd.DataFrame, *, symbol: str = "BTC/USDT") -> list[Signal]:
        base = base_fn(df, symbol=symbol)
        return llm_filtered_signals(base, df, evaluator=evaluator, threshold=threshold)

    wrapped.__name__ = f"llm_filtered({getattr(base_fn, '__name__', 'base')})"
    return wrapped


def llm_filtered_signals(
    base_signals: list[Signal],
    df: pd.DataFrame,
    *,
    evaluator: LLMEvaluator,
    threshold: float = 0.3,
) -> list[Signal]:
    """Route every `buy` through `evaluator`. Approved → pass; rejected → hold.

    `threshold` is on the verdict score (in [-1, 1]). Default 0.3 is "mildly positive".
    """
    if len(base_signals) != len(df):
        raise ValueError(f"len(base_signals)={len(base_signals)} != len(df)={len(df)}")

    out: list[Signal] = []
    for i, sig in enumerate(base_signals):
        if sig.side != "buy":
            out.append(sig)
            continue

        ctx = _build_context(df, i, sig)
        verdict: Verdict = evaluator.evaluate(sig, ctx)

        if verdict.score >= threshold:
            out.append(
                replace(
                    sig,
                    rationale=f"{sig.rationale} | llm({verdict.score:+.2f}): {verdict.rationale}",
                )
            )
        else:
            out.append(
                Signal(
                    timestamp_ms=sig.timestamp_ms,
                    symbol=sig.symbol,
                    side="hold",
                    conviction=0.0,
                    stop=None,
                    target=None,
                    rationale=f"llm rejected({verdict.score:+.2f}): {verdict.rationale}",
                )
            )
    return out
