"""Periodic news-fetch orchestrator.

Pulls every source listed in `data/news_sources.py`, tags each article with
the symbols its text mentions, scores sentiment, and persists to
`data/state/news.db`. One fetched article can produce N rows (one per
matched symbol) — that's intentional so per-symbol queries are cheap.

Run manually:
    uv run python -m tools.news_fetcher

Run via cron (recommended every 30 min during active hours):
    7 6-22 * * * cd /path && uv run python -m tools.news_fetcher
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.news_sentiment import score as score_sentiment  # noqa: E402
from data.news_sources import RawArticle, fetch_all  # noqa: E402
from data.news_store import NewsItem, NewsStore  # noqa: E402
from data.symbol_keywords import tag_symbols  # noqa: E402


def to_news_items(raw: list[RawArticle]) -> list[NewsItem]:
    """Tag each raw article with all symbols it mentions; one NewsItem per
    (article, symbol) pair so the table is queryable per-symbol without a
    JOIN. Articles that match no symbols are dropped — we only track news
    that's relevant to something we trade."""
    items: list[NewsItem] = []
    for art in raw:
        haystack = (art.headline or "") + " " + (art.body or "")
        tags = tag_symbols(haystack)
        if not tags:
            continue
        sent = score_sentiment(haystack)
        for sym in tags:
            items.append(
                NewsItem(
                    timestamp_ms=art.timestamp_ms,
                    symbol=sym,
                    source=art.source,
                    headline=art.headline,
                    url=art.url,
                    body=art.body,
                    sentiment_score=sent,
                )
            )
    return items


def main() -> int:
    print("Fetching news…", flush=True)
    raw = fetch_all()
    print(f"  fetched {len(raw)} raw articles", flush=True)
    items = to_news_items(raw)
    print(f"  matched {len(items)} symbol-tagged items", flush=True)

    store = NewsStore()
    fetched_at = int(time.time() * 1000)
    new_rows = store.add(items, fetched_at_ms=fetched_at)
    print(f"  inserted {new_rows} new rows ({store.count()} total in DB)", flush=True)

    # Per-symbol breakdown
    if items:
        from collections import Counter
        per_sym = Counter(it.symbol for it in items)
        print("  per-symbol matches this run:")
        for sym, n in per_sym.most_common():
            print(f"    {sym:12s} {n}")

    # Track "last fetch" per source so future runs can be incremental.
    sources = {it.source for it in items}
    for s in sources:
        store.mark_fetched(s, fetched_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
