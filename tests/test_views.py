"""Tests for ui.views — pure summary functions over decision-log rows."""

from __future__ import annotations

import pytest

from ui.views import event_counts, fills_dataframe, summary, trades_dataframe


def _row(**kw) -> dict:
    base = {
        "id": kw.get("id", 1),
        "timestamp_ms": kw.get("ts", 1000),
        "event_type": "signal",
        "symbol": "BTC/USDT",
        "side": None,
        "strategy_id": "test",
        "signal_id": None,
        "client_order_id": None,
        "price": None,
        "quantity": None,
        "notional": None,
        "pnl": None,
        "slippage_bps": None,
        "rationale": None,
        "metadata_json": None,
    }
    base.update(kw)
    base.pop("ts", None)
    return base


def test_event_counts_buckets_by_type() -> None:
    rows = [
        _row(event_type="signal"),
        _row(event_type="signal"),
        _row(event_type="order_filled"),
        _row(event_type="risk_block"),
    ]
    assert event_counts(rows) == {"signal": 2, "order_filled": 1, "risk_block": 1}


def test_trades_dataframe_pairs_buy_then_sell() -> None:
    rows = [
        _row(event_type="order_filled", side="buy", price=100.0, quantity=1.0, ts=1000),
        _row(
            event_type="order_filled",
            side="sell",
            price=110.0,
            quantity=1.0,
            ts=2000,
            rationale="target_hit",
        ),
    ]
    trades = trades_dataframe(rows)
    assert len(trades) == 1
    assert trades.iloc[0]["pnl"] == pytest.approx(10.0)
    assert trades.iloc[0]["return_pct"] == pytest.approx(0.1)
    assert trades.iloc[0]["exit_reason"] == "target_hit"


def test_trades_dataframe_handles_dangling_buy() -> None:
    """Open position at end → not yet a closed trade."""
    rows = [_row(event_type="order_filled", side="buy", price=100.0, quantity=1.0)]
    assert trades_dataframe(rows).empty


def test_trades_dataframe_multiple_pairs() -> None:
    rows = [
        _row(event_type="order_filled", side="buy", price=100, quantity=1, ts=1),
        _row(
            event_type="order_filled",
            side="sell",
            price=105,
            quantity=1,
            ts=2,
            rationale="signal_exit",
        ),
        _row(event_type="order_filled", side="buy", price=110, quantity=2, ts=3),
        _row(
            event_type="order_filled",
            side="sell",
            price=108,
            quantity=2,
            ts=4,
            rationale="stop_hit",
        ),
    ]
    trades = trades_dataframe(rows)
    assert len(trades) == 2
    assert trades.iloc[0]["pnl"] == 5.0
    assert trades.iloc[1]["pnl"] == -4.0


def test_summary_aggregates_correctly() -> None:
    rows = [
        _row(event_type="signal"),
        _row(event_type="order_filled", side="buy", price=100, quantity=1),
        _row(event_type="order_filled", side="sell", price=110, quantity=1, rationale="target"),
        _row(event_type="risk_block", rationale="kill_switch"),
        _row(event_type="risk_block", rationale="daily_drawdown_halt"),
        _row(event_type="risk_block", rationale="kill_switch"),
    ]
    s = summary(rows, initial_cash=10_000.0)
    assert s["trades"] == 1
    assert s["wins"] == 1
    assert s["losses"] == 0
    assert s["realized_pnl"] == 10.0
    assert s["ending_cash_estimate"] == 10_010.0
    assert s["blocks_by_reason"] == {"kill_switch": 2, "daily_drawdown_halt": 1}


def test_fills_dataframe_filters_only_fills() -> None:
    rows = [
        _row(event_type="signal"),
        _row(event_type="order_filled", side="buy", price=100, quantity=1),
        _row(event_type="risk_block"),
    ]
    fills = fills_dataframe(rows)
    assert len(fills) == 1
    assert fills.iloc[0]["side"] == "buy"


def test_fills_dataframe_empty_input() -> None:
    df = fills_dataframe([])
    assert df.empty
    assert "side" in df.columns
