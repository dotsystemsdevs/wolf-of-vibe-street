"""Binance OHLCV fetcher (CCXT, public REST — no auth needed)."""

from __future__ import annotations

from typing import Protocol, TypedDict

import ccxt

VALID_TIMEFRAMES = frozenset(
    {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}
)


class Bar(TypedDict):
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class OHLCVClient(Protocol):
    def fetch_ohlcv(
        self, symbol: str, timeframe: str = ..., since: int | None = ..., limit: int | None = ...
    ) -> list[list[float]]: ...


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "1h",
    limit: int = 500,
    *,
    client: OHLCVClient | None = None,
) -> list[Bar]:
    """Fetch OHLCV bars from Binance.

    `client` is injectable for tests; defaults to `ccxt.binance()`.
    Validates inputs upfront so a malformed call fails locally, not after a network round-trip.
    """
    if not isinstance(symbol, str) or "/" not in symbol:
        raise ValueError(f"Invalid symbol format: {symbol!r} (expected e.g. 'BTC/USDT')")
    if timeframe not in VALID_TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe!r}")
    if limit < 1 or limit > 1000:
        raise ValueError(f"limit must be 1..1000, got {limit}")

    client = client or ccxt.binance()
    raw = client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    return [
        Bar(
            timestamp_ms=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        for row in raw
    ]
