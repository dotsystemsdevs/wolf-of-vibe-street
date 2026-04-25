"""Tests for the LLM-filter strategy + RuleBasedEvaluator. No network required."""

from __future__ import annotations

import pandas as pd
import pytest

from agents.llm_evaluator import (
    EVALUATOR_SYSTEM_PROMPT,
    ClaudeEvaluator,
    RuleBasedEvaluator,
    Verdict,
    _render_prompt,
)
from data.binance import Bar
from features.compute import bars_to_df
from signals.types import Signal
from strategies.llm_filtered import llm_filtered_signals

HOUR_MS = 3_600_000


def _bars(closes: list[float]) -> pd.DataFrame:
    return bars_to_df(
        [
            Bar(timestamp_ms=i * HOUR_MS, open=c, high=c + 1, low=c - 1, close=c, volume=1.0)
            for i, c in enumerate(closes)
        ]
    )


def _buy(ts: int, conviction: float, *, stop: float = 95.0, target: float = 110.0) -> Signal:
    return Signal(ts, "BTC/USDT", "buy", conviction, stop, target, "ema cross")


def _sell(ts: int) -> Signal:
    return Signal(ts, "BTC/USDT", "sell", 0.0, None, None, "exit")


def _hold(ts: int) -> Signal:
    return Signal(ts, "BTC/USDT", "hold", 0.0, None, None, "")


# --- RuleBasedEvaluator ---


def test_rule_based_approves_high_conviction() -> None:
    e = RuleBasedEvaluator(approve_above=0.5)
    v = e.evaluate(_buy(0, conviction=0.8), {})
    assert v.score == 1.0
    assert "0.80" in v.rationale


def test_rule_based_rejects_low_conviction() -> None:
    e = RuleBasedEvaluator(approve_above=0.5)
    v = e.evaluate(_buy(0, conviction=0.2), {})
    assert v.score == -1.0


# --- llm_filtered_signals ---


def test_llm_filter_approves_buys_above_threshold() -> None:
    """Conviction 0.8 with rule-based approver passes through; rationale gets enriched."""
    df = _bars([100.0] * 50)
    sigs = [_hold(i * HOUR_MS) for i in range(50)]
    sigs[10] = _buy(10 * HOUR_MS, conviction=0.8)

    out = llm_filtered_signals(sigs, df, evaluator=RuleBasedEvaluator(0.5), threshold=0.5)
    assert out[10].side == "buy"
    assert "llm(+1.00)" in out[10].rationale
    assert out[10].stop == 95.0


def test_llm_filter_demotes_rejected_buys_to_hold() -> None:
    df = _bars([100.0] * 50)
    sigs = [_hold(i * HOUR_MS) for i in range(50)]
    sigs[10] = _buy(10 * HOUR_MS, conviction=0.2)

    out = llm_filtered_signals(sigs, df, evaluator=RuleBasedEvaluator(0.5), threshold=0.5)
    assert out[10].side == "hold"
    assert "llm rejected" in out[10].rationale
    assert out[10].stop is None


def test_llm_filter_passes_through_sell_and_hold() -> None:
    """Only buys go through the evaluator; sells/holds are untouched."""
    df = _bars([100.0] * 5)

    class _ExplodingEvaluator:
        def evaluate(self, signal: Signal, context: dict) -> Verdict:
            raise AssertionError("evaluator must not be called for non-buy signals")

    sigs = [_hold(0), _sell(HOUR_MS), _hold(2 * HOUR_MS), _hold(3 * HOUR_MS), _hold(4 * HOUR_MS)]
    out = llm_filtered_signals(sigs, df, evaluator=_ExplodingEvaluator(), threshold=0.5)
    assert [s.side for s in out] == ["hold", "sell", "hold", "hold", "hold"]


def test_llm_filter_length_mismatch_raises() -> None:
    df = _bars([100.0, 101.0])
    with pytest.raises(ValueError, match="len"):
        llm_filtered_signals([_hold(0)], df, evaluator=RuleBasedEvaluator())


# --- ClaudeEvaluator construction (no network call) ---


def test_claude_evaluator_is_configured_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ClaudeEvaluator.is_configured() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert ClaudeEvaluator.is_configured() is True


def test_claude_evaluator_default_model_is_opus_4_7(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per skill rules: default to claude-opus-4-7 unless explicitly overridden."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    e = ClaudeEvaluator()
    assert e.model == "claude-opus-4-7"


def test_render_prompt_contains_key_fields() -> None:
    sig = _buy(0, conviction=0.5, stop=95.0, target=110.0)
    ctx = {
        "entry_price": 100.0,
        "stop_pct": -0.05,
        "target_pct": 0.10,
        "rr_ratio": 2.0,
        "recent_closes": [99, 100, 101, 100, 100],
        "ema_fast": 100.5,
        "ema_slow": 99.8,
        "rsi": 55.0,
        "atr": 1.5,
        "regime": "low",
    }
    text = _render_prompt(sig, ctx)
    for token in (
        "BTC/USDT",
        "buy",
        "Entry: 100.0",
        "RSI(14): 55.0",
        "ATR(14): 1.5",
        "regime",
        "ema cross",
    ):
        assert token in text, f"missing {token!r} in:\n{text}"


def test_evaluator_system_prompt_is_static() -> None:
    """Cached system prompt must not contain per-request data — would invalidate cache."""
    for token in ("datetime", "uuid", "timestamp_ms", "request_id"):
        assert token not in EVALUATOR_SYSTEM_PROMPT, (
            f"system prompt must be byte-stable for cache; found {token!r}"
        )
