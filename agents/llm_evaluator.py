"""LLM-based signal evaluator (S-33: hybrid trigger + LLM evaluator).

The base strategy (cheap rule) decides which setups are *candidates*. The evaluator
looks at each candidate plus context and returns a score in [-1, +1] (S-58). Above a
threshold → take the trade; below → skip.

`LLMEvaluator` is a Protocol. `ClaudeEvaluator` calls the real Anthropic API with a
cached system prompt (S-02 provider-abstracted; we'll add OpenAI/etc. variants when
needed). `RuleBasedEvaluator` is a deterministic stub for tests/CI — no API key needed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic

from signals.types import Signal

EVALUATOR_SYSTEM_PROMPT = """You are a disciplined, risk-aware crypto trading evaluator.

You receive a candidate buy signal from a baseline strategy and must decide whether to
endorse it. Output ONLY a JSON object with this shape:
{"score": <float in [-1, 1]>, "rationale": "<short, concrete sentence>"}.

Score interpretation:
- +1.0 strong endorsement: setup is clean and edge looks real
-  0.0 neutral: no clear edge either way
- -1.0 strong rejection: setup is weak or already faded

Rules of engagement:
- Be conservative. Doubt is fine — when uncertain, score 0 or below.
- Never invent fundamentals or news you weren't given. Reason only from the numbers
  in the user message.
- Honor the strategy's R/R: if the trade only works because of the size of the target,
  not the quality of the setup, that's a weak setup.
- One sentence rationale, max ~30 words. No preamble, no markdown."""

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["score", "rationale"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class Verdict:
    score: float
    rationale: str


class LLMEvaluator(Protocol):
    def evaluate(self, signal: Signal, context: dict[str, Any]) -> Verdict: ...


@dataclass(frozen=True, slots=True)
class RuleBasedEvaluator(LLMEvaluator):
    """Deterministic stub for tests + dev. Approves when conviction >= threshold."""

    approve_above: float = 0.5

    def evaluate(self, signal: Signal, context: dict[str, Any]) -> Verdict:
        if signal.conviction >= self.approve_above:
            return Verdict(
                score=1.0,
                rationale=f"rule: conviction {signal.conviction:.2f} >= {self.approve_above}",
            )
        return Verdict(
            score=-1.0,
            rationale=f"rule: conviction {signal.conviction:.2f} < {self.approve_above}",
        )


def _render_prompt(signal: Signal, context: dict[str, Any]) -> str:
    """Build the per-signal user prompt. Volatile content goes here, never in system."""
    lines = [
        f"Symbol: {signal.symbol}",
        f"Side: {signal.side}",
        f"Entry: {context.get('entry_price')}",
        f"Stop: {signal.stop} ({context.get('stop_pct'):+.2%} from entry)"
        if signal.stop is not None and "stop_pct" in context
        else f"Stop: {signal.stop}",
        f"Target: {signal.target} ({context.get('target_pct'):+.2%} from entry)"
        if signal.target is not None and "target_pct" in context
        else f"Target: {signal.target}",
        f"Reward/Risk: {context.get('rr_ratio'):.1f}:1" if "rr_ratio" in context else "",
        f"Recent 5 closes: {context.get('recent_closes')}",
        f"EMA fast/slow: {context.get('ema_fast')} / {context.get('ema_slow')}",
        f"RSI(14): {context.get('rsi')}",
        f"ATR(14): {context.get('atr')}",
        f"Volatility regime: {context.get('regime')}",
        f"Strategy claim: {signal.rationale}",
    ]
    return "\n".join(line for line in lines if line)


class ClaudeEvaluator:
    """Calls Claude with a cached system prompt + JSON-schema output."""

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        *,
        model: str = "claude-opus-4-7",
        max_tokens: int = 1024,
    ):
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    @classmethod
    def is_configured(cls) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())

    def evaluate(self, signal: Signal, context: dict[str, Any]) -> Verdict:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": EVALUATOR_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
            messages=[{"role": "user", "content": _render_prompt(signal, context)}],
        )
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
        return Verdict(score=float(data["score"]), rationale=str(data["rationale"]))
