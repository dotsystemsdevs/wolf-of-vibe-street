"""LLM conviction-multiplier (Phase 4).

When a strategy emits a BUY/SHORT, this module asks Claude to weigh the setup
against fresh context (news sentiment, regime, recent strategy P&L) and
returns a `multiplier ∈ [0.3, 1.5]`. The Executor multiplies the rule-based
position size by this number — never sets it to 0. That's the deliberate
distinction from `agents/llm_evaluator`'s veto-style filter: research consensus
is that LLM-as-veto is the #1 reason agentic bots go silent. As a SIZING
modifier the LLM can add or subtract conviction without ever blocking a trade.

Output is bounded so the LLM can't accidentally turn a 0.5%-risk position
into 5% by hallucinating a 10× multiplier. The bounds are also why we don't
need a separate "kill switch" path — any verdict still results in a real
trade at some sane size.

Caching strategy mirrors `agents/llm_cache.VerdictCache`: persist across
restarts, key on `(symbol, signal_id, prompt_version)`. Live loop replays
shouldn't re-hit the API.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any

import anthropic

from agents.llm_cache import VerdictCache
from signals.types import Signal

CONVICTION_SYSTEM_PROMPT = """You are a position-sizing assistant for a paper-trading crypto bot.

You receive a candidate trade signal, the bot's recent context, and a sentiment
snapshot. Your job is to score CONVICTION as a multiplier on the rule-based
position size — between 0.3 and 1.5.

Output ONLY a JSON object:
{"multiplier": <float in [0.3, 1.5]>, "reasoning": "<one short sentence>"}

Calibration:
- 1.0 means "no opinion, size as the rules say"
- 1.3-1.5 means "this setup looks unusually clean — size up"
- 0.5-0.8 means "I see contradicting signals — size down"
- 0.3 is the floor — even on bad setups, take the trade smaller, never zero

Rules:
- You never veto. You always return a multiplier inside [0.3, 1.5].
- Reason ONLY from the numbers + headlines in the user message. Don't invent
  news you weren't given.
- Recent strategy P&L is highly informative — if a strategy is on a losing
  streak, sizing down is reasonable.
- Bullish news with a long signal → boost. Bearish news with a short → boost.
  Mixed → neutral. News contradicting the trade direction → reduce.
- One-sentence reasoning. No markdown. No preamble.
"""

PROMPT_VERSION = "conviction-v1-2026-05-10"

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "multiplier": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["multiplier", "reasoning"],
    "additionalProperties": False,
}

# Anthropic public list pricing per 1M tokens. Used for daily-spend display only.
_PRICING_USD_PER_MTOK = {
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
}


def _model_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    p = _PRICING_USD_PER_MTOK.get(model, {"input": 0.0, "output": 0.0})
    return input_tokens / 1_000_000 * p["input"] + output_tokens / 1_000_000 * p["output"]


@dataclass(frozen=True, slots=True)
class ConvictionVerdict:
    multiplier: float
    reasoning: str
    model: str = ""
    prompt_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cached: bool = False
    fallback: bool = False

    def to_metadata(self) -> dict[str, Any]:
        """Compact form for embedding in DecisionEvent metadata."""
        return asdict(self)


def _clamp(x: float, lo: float = 0.3, hi: float = 1.5) -> float:
    return max(lo, min(hi, x))


def _render_user_prompt(
    signal: Signal,
    *,
    entry_price: float,
    rr_ratio: float | None,
    regime: str | None,
    news_summary: dict | None,
    news_headlines: list[str],
    strategy_recent_pnl: dict | None,
) -> str:
    lines = [
        f"Symbol: {signal.symbol}",
        f"Side: {signal.side}",
        f"Entry: {entry_price:.4f}",
        f"Stop: {signal.stop} · Target: {signal.target}",
    ]
    if rr_ratio is not None:
        lines.append(f"Reward/Risk: {rr_ratio:.2f}:1")
    if regime:
        lines.append(f"Regime (1h): {regime}")
    if news_summary and news_summary.get("n_articles"):
        lines.append(
            f"News 24h: {news_summary['n_articles']} articles, "
            f"avg sentiment {news_summary['avg_score']:+.2f} "
            f"(min {news_summary['min_score']:+.2f}, max {news_summary['max_score']:+.2f})"
        )
    else:
        lines.append("News 24h: no recent coverage")
    if news_headlines:
        lines.append("Recent headlines:")
        for h in news_headlines[:5]:
            lines.append(f"  - {h[:140]}")
    if strategy_recent_pnl is not None:
        lines.append(
            f"This strategy's recent P&L: 24h={strategy_recent_pnl.get('pnl_24h', 0):+.2f}, "
            f"7d={strategy_recent_pnl.get('pnl_7d', 0):+.2f}, "
            f"PF={strategy_recent_pnl.get('profit_factor', 0):.2f}"
        )
    lines.append(f"Strategy rationale: {signal.rationale}")
    return "\n".join(lines)


@dataclass
class ConvictionMultiplier:
    client: anthropic.Anthropic | None = None
    model: str = "claude-haiku-4-5"
    max_tokens: int = 256
    timeout_s: float = 8.0
    cache: VerdictCache | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = anthropic.Anthropic(timeout=self.timeout_s)
        if self.cache is None:
            from pathlib import Path  # noqa: PLC0415

            self.cache = VerdictCache(
                Path(__file__).resolve().parent.parent
                / "data" / "state" / "conviction_cache.json"
            )

    @classmethod
    def is_configured(cls) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())

    def evaluate(
        self,
        signal: Signal,
        *,
        entry_price: float,
        rr_ratio: float | None = None,
        regime: str | None = None,
        news_summary: dict | None = None,
        news_headlines: list[str] | None = None,
        strategy_recent_pnl: dict | None = None,
    ) -> ConvictionVerdict:
        sid = f"{signal.symbol}:{signal.timestamp_ms}"
        cached = self.cache.get(sid, str(signal.timestamp_ms), PROMPT_VERSION)
        if cached is not None:
            d = dict(cached)
            d["cached"] = True
            return ConvictionVerdict(**d)

        user_prompt = _render_user_prompt(
            signal,
            entry_price=entry_price,
            rr_ratio=rr_ratio,
            regime=regime,
            news_summary=news_summary,
            news_headlines=news_headlines or [],
            strategy_recent_pnl=strategy_recent_pnl,
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                timeout=self.timeout_s,
                system=[
                    {
                        "type": "text",
                        "text": CONVICTION_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                output_config={
                    "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}
                },
                messages=[{"role": "user", "content": user_prompt}],
            )
        except (anthropic.APIError, anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
            # On any API error, neutral (1.0) so the rule-based size goes through unchanged.
            return ConvictionVerdict(
                multiplier=1.0,
                reasoning=f"fallback: {type(e).__name__}",
                model=self.model,
                prompt_version=PROMPT_VERSION,
                fallback=True,
            )

        text = next(b.text for b in response.content if b.type == "text")
        try:
            data = json.loads(text)
            mult = _clamp(float(data["multiplier"]))
            reasoning = str(data["reasoning"])
        except (json.JSONDecodeError, KeyError, ValueError):
            return ConvictionVerdict(
                multiplier=1.0,
                reasoning="fallback: malformed response",
                model=self.model,
                prompt_version=PROMPT_VERSION,
                fallback=True,
            )

        usage = getattr(response, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0)) if usage else 0
        out_tok = int(getattr(usage, "output_tokens", 0)) if usage else 0
        verdict = ConvictionVerdict(
            multiplier=mult,
            reasoning=reasoning,
            model=self.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=_model_cost_usd(self.model, in_tok, out_tok),
        )
        self.cache.put(sid, str(signal.timestamp_ms), PROMPT_VERSION, verdict)
        return verdict
