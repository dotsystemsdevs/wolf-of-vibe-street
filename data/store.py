"""Parquet-backed bar storage under `data/bars/{exchange}/{symbol}/{timeframe}.parquet`."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from data.binance import Bar

BARS_ROOT = Path(__file__).resolve().parent / "bars"

BAR_COLUMNS = ["timestamp_ms", "open", "high", "low", "close", "volume"]


def bars_path(exchange: str, symbol: str, timeframe: str, *, root: Path = BARS_ROOT) -> Path:
    """Canonical path for one (exchange, symbol, timeframe) Parquet file."""
    if "/" not in symbol:
        raise ValueError(f"Invalid symbol format: {symbol!r}")
    safe_symbol = symbol.replace("/", "_")
    return root / exchange / safe_symbol / f"{timeframe}.parquet"


def save_bars(bars: list[Bar], path: Path) -> int:
    """Write bars to Parquet, dedup-by-timestamp, sort ascending. Returns row count written.

    Merges with an existing file at `path` (so re-running a backfill is idempotent).
    """
    new_df = pd.DataFrame(bars, columns=BAR_COLUMNS)
    if path.exists():
        existing = pd.read_parquet(path)
        df = pd.concat([existing, new_df], ignore_index=True)
    else:
        df = new_df
    df = (
        df.drop_duplicates(subset="timestamp_ms").sort_values("timestamp_ms").reset_index(drop=True)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, compression="snappy")
    return len(df)


def load_bars(path: Path) -> list[Bar]:
    """Read bars back as a list of Bar dicts (sorted ascending by timestamp_ms)."""
    if not path.exists():
        return []
    df = pd.read_parquet(path).sort_values("timestamp_ms").reset_index(drop=True)
    return [
        Bar(
            timestamp_ms=int(row.timestamp_ms),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in df.itertuples(index=False)
    ]
