"""Paginated OHLCV backfill from Binance (CCXT public REST)."""

from __future__ import annotations

import time

import ccxt

from data.binance import VALID_TIMEFRAMES, Bar, OHLCVClient

TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
    "1M": 2_592_000_000,
}


def backfill_ohlcv(
    symbol: str,
    timeframe: str = "1h",
    *,
    since_ms: int,
    until_ms: int | None = None,
    client: OHLCVClient | None = None,
    chunk_size: int = 1000,
    sleep_s: float = 0.0,
) -> list[Bar]:
    """Page through OHLCV history from `since_ms` (inclusive) up to `until_ms` (exclusive).

    Stops when (a) the exchange returns fewer than `chunk_size` rows, (b) the next page
    would start at or past `until_ms`, or (c) no progress is made (defensive against an
    exchange returning the same `since` repeatedly).

    Caller is responsible for `enableRateLimit=True` on a real client; `sleep_s` is an
    extra spacer between pages if needed.
    """
    if not isinstance(symbol, str) or "/" not in symbol:
        raise ValueError(f"Invalid symbol format: {symbol!r}")
    if timeframe not in VALID_TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe!r}")
    if chunk_size < 1 or chunk_size > 1000:
        raise ValueError(f"chunk_size must be 1..1000, got {chunk_size}")
    if since_ms < 0:
        raise ValueError(f"since_ms must be >= 0, got {since_ms}")
    if until_ms is not None and until_ms <= since_ms:
        raise ValueError(f"until_ms ({until_ms}) must be > since_ms ({since_ms})")

    client = client or ccxt.binance({"enableRateLimit": True})
    tf_ms = TIMEFRAME_MS[timeframe]

    bars: list[Bar] = []
    cursor = since_ms
    last_ts: int | None = None

    while True:
        page = client.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=chunk_size)
        if not page:
            break

        for row in page:
            ts = int(row[0])
            if last_ts is not None and ts <= last_ts:
                continue
            if until_ms is not None and ts >= until_ms:
                return bars
            bars.append(
                Bar(
                    timestamp_ms=ts,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
            last_ts = ts

        if len(page) < chunk_size:
            break
        next_cursor = (last_ts or cursor) + tf_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor

        if sleep_s > 0:
            time.sleep(sleep_s)

    return bars
