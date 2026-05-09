"""Tests for agents.conviction — LLM conviction-multiplier (Phase 4)."""

from __future__ import annotations

from agents.conviction import (
    ConvictionVerdict,
    _clamp,
    _render_user_prompt,
)
from signals.types import Signal


def _buy(ts: int = 1000) -> Signal:
    return Signal(
        timestamp_ms=ts, symbol="BTC/USDT", side="buy", conviction=0.7,
        stop=95.0, target=110.0, rationale="rsi cross-up from oversold (28)",
    )


# --- expected ---


def test_clamp_keeps_value_in_range() -> None:
    assert _clamp(1.0) == 1.0
    assert _clamp(0.5) == 0.5
    assert _clamp(0.3) == 0.3
    assert _clamp(1.5) == 1.5


def test_clamp_floors_low() -> None:
    """LLM cannot zero out a trade. Anything below 0.3 → clamped to 0.3."""
    assert _clamp(0.0) == 0.3
    assert _clamp(-1.0) == 0.3
    assert _clamp(0.1) == 0.3


def test_clamp_caps_high() -> None:
    """LLM can't multiply position size by 5x. Anything above 1.5 → 1.5."""
    assert _clamp(2.0) == 1.5
    assert _clamp(99.0) == 1.5


def test_render_user_prompt_includes_essentials() -> None:
    sig = _buy()
    prompt = _render_user_prompt(
        sig,
        entry_price=100.0,
        rr_ratio=2.5,
        regime="uptrend",
        news_summary={
            "n_articles": 5,
            "avg_score": 0.4,
            "min_score": -0.2,
            "max_score": 0.8,
        },
        news_headlines=["Bitcoin hits new high", "ETF approval imminent"],
        strategy_recent_pnl={"pnl_24h": 50.0, "pnl_7d": 100.0, "profit_factor": 1.4},
    )
    for token in (
        "BTC/USDT", "buy", "Entry: 100", "uptrend", "5 articles",
        "+0.40", "Bitcoin hits", "Reward/Risk: 2.50", "PF=1.40",
    ):
        assert token in prompt, f"missing {token!r}"


# --- edge ---


def test_render_handles_no_news() -> None:
    """News pipeline may have no articles for a symbol — must not crash."""
    sig = _buy()
    prompt = _render_user_prompt(
        sig,
        entry_price=100.0,
        rr_ratio=None,
        regime=None,
        news_summary=None,
        news_headlines=[],
        strategy_recent_pnl=None,
    )
    assert "no recent coverage" in prompt
    assert "BTC/USDT" in prompt


def test_render_handles_zero_articles_summary() -> None:
    """Summary with n_articles=0 must also use the fallback string."""
    sig = _buy()
    prompt = _render_user_prompt(
        sig, entry_price=100.0, rr_ratio=None, regime=None,
        news_summary={"n_articles": 0, "avg_score": 0.0, "min_score": 0.0, "max_score": 0.0},
        news_headlines=[], strategy_recent_pnl=None,
    )
    assert "no recent coverage" in prompt


# --- verdict dataclass ---


def test_verdict_to_metadata_round_trip() -> None:
    """Verdict.to_metadata produces dict that's JSON-serialisable for storing
    in the decision log's metadata column."""
    v = ConvictionVerdict(
        multiplier=1.2, reasoning="clean breakout + bullish news",
        model="claude-haiku-4-5", prompt_version="conviction-v1",
        input_tokens=100, output_tokens=20, cost_usd=0.0002,
    )
    md = v.to_metadata()
    assert md["multiplier"] == 1.2
    assert md["reasoning"] == "clean breakout + bullish news"
    assert md["cost_usd"] == 0.0002
    import json
    json.dumps(md)  # must not raise
