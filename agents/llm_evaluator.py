"""LLM-based signal evaluator (S-33: hybrid trigger + LLM evaluator).

The base strategy (cheap rule) decides which setups are *candidates*. The evaluator
looks at each candidate plus context and returns a score in [-1, +1] (S-58). Above a
threshold → take the trade; below → skip.

Production-hardened:
- **Cache** by (symbol, signal_id, prompt_version) so live-loop replays don't
  re-hit the API for already-evaluated signals.
- **Cheap model default** (Haiku) — score-in-[-1,1] is a tiny task; Opus is overkill.
- **Hard timeout** so a hung API call doesn't block trading; falls back to
  neutral (score=0) verdict so the trade follows base rule.
- **Structured cost tracking** — each verdict carries input/output token counts
  + a derived USD cost so the dashboard can show daily LLM spend.
- **Prompt-versioned** so changes invalidate old cache cleanly.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from agents.llm_cache import VerdictCache
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

# Bump this when EVALUATOR_SYSTEM_PROMPT changes — invalidates the cache so old
# verdicts under the prior prompt aren't reused. Also stamped into each verdict
# so postmortems can correlate decision quality with prompt revisions.
PROMPT_VERSION = "v1-2026-04-28"

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["score", "rationale"],
    "additionalProperties": False,
}

# Anthropic public list prices, USD per 1M tokens. Updated 2026-04-28 — bump
# whenever pricing changes or you switch models. Used purely for the dashboard's
# daily-spend display, not for any business logic.
_PRICING_USD_PER_MTOK = {
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
}


def _model_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    p = _PRICING_USD_PER_MTOK.get(model, {"input": 0.0, "output": 0.0})
    return input_tokens / 1_000_000 * p["input"] + output_tokens / 1_000_000 * p["output"]


@dataclass(frozen=True, slots=True)
class Verdict:
    score: float
    rationale: str
    # Observability fields — persisted in the cache + exposed via the decision-log
    # metadata so we can compute "what % did the LLM reject" + daily $ spend.
    model: str = ""
    prompt_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cached: bool = False
    fallback: bool = False


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
                model="rule-based",
                prompt_version=PROMPT_VERSION,
            )
        return Verdict(
            score=-1.0,
            rationale=f"rule: conviction {signal.conviction:.2f} < {self.approve_above}",
            model="rule-based",
            prompt_version=PROMPT_VERSION,
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


def _signal_id(signal: Signal) -> str:
    """Stable identifier for caching. Pure function of timestamp + symbol."""
    return f"{signal.symbol}:{signal.timestamp_ms}"


@dataclass
class ClaudeEvaluator:
    """Calls Claude with a cached system prompt + JSON-schema output.

    Defaults to Haiku — score-in-[-1,1] is a tiny classification task; Opus would
    cost 15× more input + 15× more output for marginal quality gain. Switch via
    the `model=` constructor arg if backtests show the cheap model is too lenient.
    """

    client: anthropic.Anthropic | None = None
    model: str = "claude-haiku-4-5"
    max_tokens: int = 256
    timeout_s: float = 8.0
    cache: VerdictCache | None = field(default=None)

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = anthropic.Anthropic(timeout=self.timeout_s)
        if self.cache is None:
            self.cache = VerdictCache()

    @classmethod
    def is_configured(cls) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())

    def evaluate(self, signal: Signal, context: dict[str, Any]) -> Verdict:
        # 1) Cache hit fast-path — covers historical replays during live-loop bootstrap.
        sid = _signal_id(signal)
        cached = self.cache.get(sid, str(signal.timestamp_ms), PROMPT_VERSION)
        if cached is not None:
            return Verdict(**{**cached, "cached": True})

        # 2) API call. Hard timeout via the client. On any failure, return a neutral
        #    fallback so the live loop never blocks on a hung evaluator. The fallback's
        #    score=0 means whatever threshold the operator set decides: if threshold > 0
        #    (default 0.3), neutral fails → trade is skipped. Conservative on error.
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                timeout=self.timeout_s,
                system=[
                    {
                        "type": "text",
                        "text": EVALUATOR_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                output_config={"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
                messages=[{"role": "user", "content": _render_prompt(signal, context)}],
            )
        except (anthropic.APIError, anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
            return Verdict(
                score=0.0,
                rationale=f"llm fallback: {type(e).__name__}",
                model=self.model,
                prompt_version=PROMPT_VERSION,
                fallback=True,
            )

        text = next(b.text for b in response.content if b.type == "text")
        try:
            data = json.loads(text)
            score = float(data["score"])
            rationale = str(data["rationale"])
        except (json.JSONDecodeError, KeyError, ValueError):
            return Verdict(
                score=0.0,
                rationale="llm fallback: malformed response",
                model=self.model,
                prompt_version=PROMPT_VERSION,
                fallback=True,
            )

        usage = getattr(response, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0)) if usage else 0
        out_tok = int(getattr(usage, "output_tokens", 0)) if usage else 0
        verdict = Verdict(
            score=score,
            rationale=rationale,
            model=self.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=_model_cost_usd(self.model, in_tok, out_tok),
        )
        self.cache.put(sid, str(signal.timestamp_ms), PROMPT_VERSION, verdict)
        return verdict


def context_hash(ctx: dict[str, Any]) -> str:
    """Stable fingerprint of a context dict — useful when context changes mid-bar
    (e.g., a new fill changed regime). Not currently used in the cache key but
    kept here so we can swap to a context-aware cache later without restructuring."""
    canonical = json.dumps(ctx, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
