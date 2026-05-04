"""Tests for news pipeline (Phase 3) — store, tagging, sentiment."""

from __future__ import annotations

from pathlib import Path

from data.news_sentiment import score as sentiment_score
from data.news_store import NewsItem, NewsStore
from data.symbol_keywords import tag_symbols


# --- symbol tagging ---


def test_tag_symbols_finds_full_name() -> None:
    assert "BTC/USDT" in tag_symbols("Bitcoin breaks ATH")
    assert "ETH/USDT" in tag_symbols("Ethereum upgrade lands")


def test_tag_symbols_finds_ticker() -> None:
    """Project name takes priority; bare tickers only retained where unambiguous."""
    assert "SOL/USDT" in tag_symbols("Solana surges 12% overnight")
    assert "BTC/USDT" in tag_symbols("Whales accumulate BTC")


def test_tag_symbols_word_boundary_avoids_false_positive() -> None:
    """Generic English words must NOT cause mismatches. After the 2026-05-04
    precision tightening: 'sol' inside other words doesn't fire SOL, etc."""
    assert "ADA/USDT" not in tag_symbols("The Bank of Canada announces new regulations")
    assert "LINK/USDT" not in tag_symbols("Click this link for the daily discussion")
    assert "SOL/USDT" not in tag_symbols("Gospel solo for the soloist")
    assert "NEAR/USDT" not in tag_symbols("The deadline is near for filing taxes")


def test_tag_symbols_returns_multiple() -> None:
    text = "Ethereum and Solana both rallied today; Bitcoin stayed flat"
    tags = tag_symbols(text)
    assert {"BTC/USDT", "ETH/USDT", "SOL/USDT"}.issubset(set(tags))


def test_tag_symbols_empty_text() -> None:
    assert tag_symbols("") == []


# --- sentiment scoring ---


def test_sentiment_bullish_text_positive() -> None:
    s = sentiment_score("Bitcoin rallies to ATH on ETF approval; surge continues")
    assert s > 0.5


def test_sentiment_bearish_text_negative() -> None:
    s = sentiment_score("Crash and panic as exchange hacked, investors face huge losses")
    assert s < -0.5


def test_sentiment_neutral_text_zero() -> None:
    s = sentiment_score("Bitcoin price is currently trading on multiple exchanges")
    assert s == 0.0


def test_sentiment_empty_zero() -> None:
    assert sentiment_score("") == 0.0


def test_sentiment_clamped_to_unit_range() -> None:
    """Score must always land in [-1, 1] regardless of word counts."""
    s = sentiment_score("rally rally rally rally rally rally rally rally rally")
    assert -1.0 <= s <= 1.0


# --- store ---


def _store(tmp_path: Path) -> NewsStore:
    return NewsStore(tmp_path / "news.db")


def test_store_add_dedups_by_symbol_url(tmp_path: Path) -> None:
    """Same (symbol, URL) twice → one row. Lets the cron be idempotent."""
    s = _store(tmp_path)
    item = NewsItem(
        timestamp_ms=1000, symbol="BTC/USDT", source="reddit/Bitcoin",
        headline="Bitcoin to the moon", url="https://r.example/1",
        body=None, sentiment_score=0.8,
    )
    n = s.add([item, item], fetched_at_ms=1)
    assert n == 1
    assert s.count() == 1


def test_store_recent_filters_by_window(tmp_path: Path) -> None:
    """Articles older than the window are excluded."""
    s = _store(tmp_path)
    import time
    now_ms = int(time.time() * 1000)
    items = [
        NewsItem(now_ms - 1 * 3600_000, "BTC/USDT", "rss/coindesk",
                 "fresh", "https://r/1", None, 0.5),
        NewsItem(now_ms - 48 * 3600_000, "BTC/USDT", "rss/coindesk",
                 "stale", "https://r/2", None, 0.0),
    ]
    s.add(items, fetched_at_ms=now_ms)
    recent = s.recent("BTC/USDT", window_h=24)
    assert len(recent) == 1
    assert recent[0]["headline"] == "fresh"


def test_store_summary_aggregates(tmp_path: Path) -> None:
    s = _store(tmp_path)
    import time
    now_ms = int(time.time() * 1000)
    items = [
        NewsItem(now_ms - 100, "ETH/USDT", "rss/x", "h1", "https://x/1", None, 0.5),
        NewsItem(now_ms - 200, "ETH/USDT", "rss/x", "h2", "https://x/2", None, -0.3),
        NewsItem(now_ms - 300, "ETH/USDT", "rss/x", "h3", "https://x/3", None, 0.7),
    ]
    s.add(items, fetched_at_ms=now_ms)
    summary = s.summary("ETH/USDT", window_h=24)
    assert summary["n_articles"] == 3
    assert abs(summary["avg_score"] - (0.5 - 0.3 + 0.7) / 3) < 1e-9
    assert summary["min_score"] == -0.3
    assert summary["max_score"] == 0.7


def test_store_latest_fetch_ts_round_trip(tmp_path: Path) -> None:
    s = _store(tmp_path)
    assert s.latest_fetch_ts("rss/coindesk") is None
    s.mark_fetched("rss/coindesk", 12345)
    assert s.latest_fetch_ts("rss/coindesk") == 12345
    s.mark_fetched("rss/coindesk", 67890)
    assert s.latest_fetch_ts("rss/coindesk") == 67890
