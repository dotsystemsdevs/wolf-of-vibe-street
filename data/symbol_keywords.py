"""Symbol → keyword mapping for matching news to tickers.

The fetcher gets generic crypto news; we tag each article with the symbols
its headline + body mentions. Keywords are case-insensitive whole-word
matches (re.search with \\b boundaries) so "ada" doesn't match "Canada".

Order matters: longer/more specific names first to avoid double-tagging.
Each symbol has 2-4 forms: full name, ticker, common abbreviation. Add more
as needed when articles are silently being missed.
"""

from __future__ import annotations

import re

# (symbol_id, [keyword_patterns]). Patterns are word-boundary regex.
# Bias toward precision over recall — false positives (tagging an article that
# doesn't really discuss the symbol) pollute the LLM context downstream and
# poison sentiment scores. Generic English words ("link", "near", "sol",
# "ada") are deliberately excluded; only project-specific names + tickers
# unambiguous in crypto context remain.
SYMBOL_KEYWORDS: dict[str, list[str]] = {
    "BTC/USDT":  ["bitcoin", "btc"],
    "ETH/USDT":  ["ethereum", r"\beth\b"],
    "SOL/USDT":  ["solana"],
    "ADA/USDT":  ["cardano"],
    "AVAX/USDT": ["avalanche", "avax"],
    "LINK/USDT": ["chainlink"],
    "AAVE/USDT": [r"\baave\b"],
    "NEAR/USDT": ["near protocol", r"\bnear\b crypto"],
    "INJ/USDT":  ["injective"],
    "ARB/USDT":  ["arbitrum"],
    "XRP/USDT":  ["ripple", "xrp"],
    "DOGE/USDT": ["dogecoin"],
    "BNB/USDT":  [r"\bbnb\b", "binance coin"],
    "SUI/USDT":  ["sui network", r"\bsui blockchain\b"],
}

# Pre-compile once. Each pattern matches case-insensitively at word boundary
# unless the entry already specifies \b (allowing literal word matches).
_COMPILED: list[tuple[str, re.Pattern[str]]] = []
for sym, patterns in SYMBOL_KEYWORDS.items():
    for pat in patterns:
        if r"\b" not in pat:
            pat = rf"\b{re.escape(pat)}\b"
        _COMPILED.append((sym, re.compile(pat, re.IGNORECASE)))


def tag_symbols(text: str) -> list[str]:
    """Return all symbols whose keywords appear in `text`. Order: as listed
    in SYMBOL_KEYWORDS so callers see deterministic output."""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for sym, pattern in _COMPILED:
        if sym in seen:
            continue
        if pattern.search(text):
            out.append(sym)
            seen.add(sym)
    return out
