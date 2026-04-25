"""Tests for signals.types.Signal — invariants enforced at construction."""

from __future__ import annotations

import pytest

from signals.types import Signal


def test_expected_buy_signal_with_stop_constructs() -> None:
    s = Signal(
        timestamp_ms=1000,
        symbol="BTC/USDT",
        side="buy",
        conviction=0.5,
        stop=99.0,
        target=110.0,
        rationale="x",
    )
    assert s.side == "buy"
    assert s.stop == 99.0


def test_edge_hold_signal_no_stop_ok() -> None:
    """Hold signals don't need a stop — there's no entry to protect."""
    s = Signal(
        timestamp_ms=1000,
        symbol="BTC/USDT",
        side="hold",
        conviction=0.0,
        stop=None,
        target=None,
        rationale="",
    )
    assert s.side == "hold"


def test_failure_buy_without_stop_raises() -> None:
    """S-15 / P-20: no stop → no entry."""
    with pytest.raises(ValueError, match="no stop"):
        Signal(
            timestamp_ms=1000,
            symbol="BTC/USDT",
            side="buy",
            conviction=0.5,
            stop=None,
            target=110.0,
            rationale="x",
        )


def test_edge_sell_without_stop_ok() -> None:
    """Sell = exit existing long. The stop was set on entry; none needed here."""
    s = Signal(
        timestamp_ms=1000,
        symbol="BTC/USDT",
        side="sell",
        conviction=0.0,
        stop=None,
        target=None,
        rationale="exit",
    )
    assert s.side == "sell"


def test_failure_conviction_out_of_range_raises() -> None:
    with pytest.raises(ValueError, match="conviction"):
        Signal(
            timestamp_ms=1000,
            symbol="BTC/USDT",
            side="hold",
            conviction=1.5,
            stop=None,
            target=None,
            rationale="",
        )
