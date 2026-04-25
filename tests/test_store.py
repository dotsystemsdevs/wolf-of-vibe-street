"""Tests for data.store — Parquet round-trip + path conventions + idempotent merge."""

from __future__ import annotations

from pathlib import Path

import pytest

from data.binance import Bar
from data.store import bars_path, load_bars, save_bars


def _bar(ts: int, c: float = 100.0) -> Bar:
    return Bar(timestamp_ms=ts, open=c, high=c + 1, low=c - 1, close=c + 0.5, volume=10.0)


def test_expected_round_trip_preserves_order_and_values(tmp_path: Path) -> None:
    bars = [_bar(2000), _bar(1000), _bar(3000)]
    path = tmp_path / "out.parquet"

    written = save_bars(bars, path)

    assert written == 3
    loaded = load_bars(path)
    assert [b["timestamp_ms"] for b in loaded] == [1000, 2000, 3000]
    assert loaded[0]["open"] == 100.0
    assert loaded[2]["close"] == 100.5


def test_edge_merge_dedups_against_existing_file(tmp_path: Path) -> None:
    """Re-running save with overlapping rows must not duplicate."""
    path = tmp_path / "out.parquet"
    save_bars([_bar(1000), _bar(2000)], path)

    written = save_bars([_bar(2000), _bar(3000)], path)

    assert written == 3
    loaded = load_bars(path)
    assert [b["timestamp_ms"] for b in loaded] == [1000, 2000, 3000]


def test_edge_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_bars(tmp_path / "nope.parquet") == []


def test_failure_bars_path_rejects_invalid_symbol(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        bars_path("binance", "BTCUSDT", "1h", root=tmp_path)


def test_bars_path_canonical_layout(tmp_path: Path) -> None:
    p = bars_path("binance", "BTC/USDT", "1h", root=tmp_path)
    assert p == tmp_path / "binance" / "BTC_USDT" / "1h.parquet"
