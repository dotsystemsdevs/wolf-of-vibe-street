"""News + sentiment storage layer (Phase 3).

Append-only SQLite table that the news fetcher writes to and the LLM
context-builder reads from. Schema mirrors the decision_log pattern: simple,
indexed for the queries we actually run, no schema migration framework
needed for a single-table model.

Public API:
  - NewsStore(path) — open/create DB
  - .add(items) — bulk insert with dedup by URL
  - .recent(symbol, window_h, limit) — fetch articles tagged for a symbol
  - .summary(symbol, window_h) — aggregate sentiment + count
  - .latest_fetch_ts(source) — for incremental fetching
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "state" / "news.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    headline TEXT NOT NULL,
    url TEXT NOT NULL,
    body TEXT,
    sentiment_score REAL,
    fetched_at_ms INTEGER NOT NULL,
    UNIQUE(symbol, url)
);
CREATE INDEX IF NOT EXISTS idx_news_symbol_ts ON news(symbol, timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_news_source ON news(source);

CREATE TABLE IF NOT EXISTS news_meta (
    source TEXT PRIMARY KEY,
    last_fetch_ms INTEGER NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class NewsItem:
    """One news headline matched to a symbol. `body` is optional (Reddit posts
    have selftext; RSS items may have description). `sentiment_score` is in
    [-1, 1] where +1 = bullish, -1 = bearish, 0 = neutral / not scored."""

    timestamp_ms: int
    symbol: str
    source: str
    headline: str
    url: str
    body: str | None = None
    sentiment_score: float | None = None


class NewsStore:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.executescript(SCHEMA)

    def add(self, items: list[NewsItem], *, fetched_at_ms: int) -> int:
        """Insert items, dedup-by-(symbol,url). Returns count of NEW rows."""
        if not items:
            return 0
        before = self._conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        for it in items:
            self._conn.execute(
                """INSERT OR IGNORE INTO news
                   (timestamp_ms, symbol, source, headline, url, body, sentiment_score, fetched_at_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    it.timestamp_ms,
                    it.symbol,
                    it.source,
                    it.headline,
                    it.url,
                    it.body,
                    it.sentiment_score,
                    fetched_at_ms,
                ),
            )
        after = self._conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        return after - before

    def recent(
        self, symbol: str, *, window_h: int = 24, limit: int = 20
    ) -> list[dict]:
        """Most recent articles for a symbol within the time window."""
        cutoff_ms = self._cutoff(window_h)
        cur = self._conn.execute(
            """SELECT id, timestamp_ms, symbol, source, headline, url, body, sentiment_score
               FROM news
               WHERE symbol = ? AND timestamp_ms >= ?
               ORDER BY timestamp_ms DESC
               LIMIT ?""",
            (symbol, cutoff_ms, limit),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

    def summary(self, symbol: str, *, window_h: int = 24) -> dict:
        """Aggregate stats for a symbol's recent news."""
        cutoff_ms = self._cutoff(window_h)
        cur = self._conn.execute(
            """SELECT COUNT(*) AS n,
                      AVG(sentiment_score) AS avg_score,
                      MIN(sentiment_score) AS min_score,
                      MAX(sentiment_score) AS max_score
               FROM news
               WHERE symbol = ? AND timestamp_ms >= ?
                 AND sentiment_score IS NOT NULL""",
            (symbol, cutoff_ms),
        )
        row = cur.fetchone()
        return {
            "symbol": symbol,
            "window_h": window_h,
            "n_articles": int(row[0] or 0),
            "avg_score": float(row[1]) if row[1] is not None else 0.0,
            "min_score": float(row[2]) if row[2] is not None else 0.0,
            "max_score": float(row[3]) if row[3] is not None else 0.0,
        }

    def latest_fetch_ts(self, source: str) -> int | None:
        """For incremental fetching — when did we last hit this source?"""
        row = self._conn.execute(
            "SELECT last_fetch_ms FROM news_meta WHERE source = ?", (source,)
        ).fetchone()
        return int(row[0]) if row else None

    def mark_fetched(self, source: str, ts_ms: int) -> None:
        self._conn.execute(
            """INSERT INTO news_meta (source, last_fetch_ms) VALUES (?, ?)
               ON CONFLICT(source) DO UPDATE SET last_fetch_ms = excluded.last_fetch_ms""",
            (source, ts_ms),
        )

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM news").fetchone()[0])

    def close(self) -> None:
        self._conn.close()

    def _cutoff(self, window_h: int) -> int:
        import time as _t  # noqa: PLC0415

        now_ms = int(_t.time() * 1000)
        return now_ms - window_h * 3_600_000
