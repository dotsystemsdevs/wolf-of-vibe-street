"""Tests for data.backfill — pagination + termination conditions."""

from __future__ import annotations

import ccxt
import pytest

from data.backfill import backfill_ohlcv

HOUR_MS = 3_600_000


def _row(ts: int, c: float = 100.0) -> list[float]:
    return [ts, c, c + 1, c - 1, c + 0.5, 10.0]


class _PaginatedClient:
    """Returns one page per call from `pages`. After the queue is empty, returns []."""

    def __init__(self, pages: list[list[list[float]]]):
        self.pages = list(pages)
        self.calls: list[tuple[int | None, int | None]] = []

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: int | None = None,
        limit: int | None = None,
    ) -> list[list[float]]:
        self.calls.append((since, limit))
        return self.pages.pop(0) if self.pages else []


def test_expected_paginates_until_short_page() -> None:
    """Happy: 2 pages of 3 + 2 bars; second is short → stop. Cursor advances by tf_ms."""
    page1 = [_row(1000), _row(1000 + HOUR_MS), _row(1000 + 2 * HOUR_MS)]
    page2 = [_row(1000 + 3 * HOUR_MS), _row(1000 + 4 * HOUR_MS)]
    client = _PaginatedClient([page1, page2])

    bars = backfill_ohlcv("BTC/USDT", timeframe="1h", since_ms=1000, client=client, chunk_size=3)

    assert [b["timestamp_ms"] for b in bars] == [
        1000,
        1000 + HOUR_MS,
        1000 + 2 * HOUR_MS,
        1000 + 3 * HOUR_MS,
        1000 + 4 * HOUR_MS,
    ]
    # Second call's `since` must advance past last_ts of page1 by exactly tf_ms.
    assert client.calls[0] == (1000, 3)
    assert client.calls[1] == (1000 + 2 * HOUR_MS + HOUR_MS, 3)


def test_edge_until_ms_trims_overshoot() -> None:
    """Edge: page contains rows past until_ms → trim before returning, no extra calls."""
    page = [_row(1000), _row(1000 + HOUR_MS), _row(1000 + 2 * HOUR_MS)]
    client = _PaginatedClient([page])
    until = 1000 + 2 * HOUR_MS

    bars = backfill_ohlcv("BTC/USDT", since_ms=1000, until_ms=until, client=client, chunk_size=10)

    assert [b["timestamp_ms"] for b in bars] == [1000, 1000 + HOUR_MS]
    assert len(client.calls) == 1


def test_edge_empty_first_page_returns_empty() -> None:
    """Edge: exchange returns no bars at all → []."""
    client = _PaginatedClient([[]])
    assert backfill_ohlcv("BTC/USDT", since_ms=0, client=client, chunk_size=10) == []


def test_edge_dedup_overlapping_timestamps() -> None:
    """Edge: page2 overlaps page1's last bar → dedup by timestamp."""
    page1 = [_row(1000), _row(1000 + HOUR_MS)]
    page2 = [_row(1000 + HOUR_MS), _row(1000 + 2 * HOUR_MS)]
    client = _PaginatedClient([page1, page2])

    bars = backfill_ohlcv("BTC/USDT", since_ms=1000, client=client, chunk_size=2)
    timestamps = [b["timestamp_ms"] for b in bars]
    assert timestamps == [1000, 1000 + HOUR_MS, 1000 + 2 * HOUR_MS]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"symbol": "BTCUSDT", "since_ms": 0},
        {"symbol": "BTC/USDT", "timeframe": "7m", "since_ms": 0},
        {"symbol": "BTC/USDT", "since_ms": -1},
        {"symbol": "BTC/USDT", "since_ms": 1000, "until_ms": 500},
        {"symbol": "BTC/USDT", "since_ms": 0, "chunk_size": 0},
        {"symbol": "BTC/USDT", "since_ms": 0, "chunk_size": 1001},
    ],
)
def test_failure_invalid_inputs_raise(kwargs: dict) -> None:
    client = _PaginatedClient([[_row(1000)]])
    with pytest.raises(ValueError):
        backfill_ohlcv(client=client, **kwargs)
    assert client.calls == []


def test_failure_network_error_propagates() -> None:
    class _ExplodingClient:
        def fetch_ohlcv(self, *args: object, **kwargs: object) -> list[list[float]]:
            raise ccxt.NetworkError("upstream down")

    with pytest.raises(ccxt.NetworkError, match="upstream down"):
        backfill_ohlcv("BTC/USDT", since_ms=0, client=_ExplodingClient())
