"""Lightweight word-list sentiment scorer (Phase 3 baseline).

Returns a score in [-1, +1] based on counts of crypto-bullish vs crypto-bearish
words in the text. Deterministic, fast, no external deps. Acts as a baseline
until Phase 4 wires the LLM as the real sentiment-extractor.

This is intentionally crude — it'll mis-score sarcasm, irony, and any nuanced
take. The point isn't accuracy; it's having SOMETHING numeric in the news
table so dashboards + queries work end-to-end. The LLM in Phase 4 will rescore.
"""

from __future__ import annotations

import re

# Words that nudge the score positive when they appear in a headline. Crypto-
# specific (mainnet, partnership, ATH) plus generic financial-positive (rally,
# surge, gain). Negative list mirrors with the opposing sentiment.
_BULLISH = {
    "rally", "surge", "soar", "spike", "moon", "pump", "gains", "gain",
    "ath", "all-time high", "high", "highs", "breakout", "bullish", "bull",
    "rebound", "recovery", "recover", "approval", "approved", "etf",
    "partnership", "partners", "adoption", "launch", "mainnet", "upgrade",
    "milestone", "record", "buy", "buying", "support", "supports",
    "outperform", "winner", "wins", "profit", "profitable", "boost", "boosted",
    "jumps", "jump", "soars", "rises", "rise", "rising", "climb", "climbing",
}

_BEARISH = {
    "crash", "plunge", "dump", "dumps", "tank", "tanks", "tanking", "drop",
    "drops", "fall", "falls", "falling", "fell", "decline", "declines",
    "declining", "declined", "low", "lows", "bear", "bearish", "breakdown",
    "selloff", "sell-off", "liquidation", "liquidations", "liquidated",
    "scam", "hack", "hacked", "exploit", "exploited", "rug", "rugpull",
    "ban", "banned", "regulator", "regulators", "investigation", "lawsuit",
    "fraud", "ponzi", "loss", "losses", "down", "wreckage", "collapse",
    "collapsed", "tumble", "tumbles", "tumbling", "slide", "slides",
    "weakness", "weak", "fear", "panic", "concern", "concerns", "worry",
}


_WORD_RE = re.compile(r"\b[\w-]+\b")


def score(text: str) -> float:
    """Return a sentiment score in [-1, +1]. Empty or unmatched text → 0.0."""
    if not text:
        return 0.0
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return 0.0
    pos = sum(1 for w in words if w in _BULLISH)
    neg = sum(1 for w in words if w in _BEARISH)
    if pos + neg == 0:
        return 0.0
    # Net direction normalized by total emotional words; clamp to [-1, 1].
    raw = (pos - neg) / max(pos + neg, 1)
    return max(-1.0, min(1.0, raw))
