"""Tests for execution.ccxt_paper.PaperBroker — fills, idempotency, position math."""

from __future__ import annotations

from execution.broker import Order
from execution.ccxt_paper import PaperBroker


def _broker(price: float = 100.0, slippage: float = 5.0, commission: float = 10.0) -> PaperBroker:
    return PaperBroker(
        get_price=lambda _: price,
        slippage_bps=slippage,
        commission_bps=commission,
        clock_ms=lambda: 1000,
    )


def test_expected_buy_fills_with_slippage_and_fee() -> None:
    b = _broker(price=100.0, slippage=10.0, commission=10.0)
    fill = b.place(Order(client_order_id="c1", symbol="BTC/USDT", side="buy", quantity=1.0))
    assert fill is not None
    assert fill.price == 100.1  # 10 bps slippage on a buy
    assert fill.fee == 100.1 * 1.0 * 0.001
    assert fill.symbol == "BTC/USDT"


def test_expected_sell_fills_with_negative_slippage() -> None:
    b = _broker(price=100.0, slippage=10.0)
    fill = b.place(Order(client_order_id="c1", symbol="BTC/USDT", side="sell", quantity=1.0))
    assert fill is not None
    assert fill.price == 99.9


def test_idempotency_same_coid_returns_same_fill() -> None:
    """I-3: re-placing an order with the same coid returns the original fill, no double-fill."""
    b = _broker()
    o = Order(client_order_id="dup", symbol="BTC/USDT", side="buy", quantity=1.0)

    f1 = b.place(o)
    f2 = b.place(o)

    assert f1 is f2
    positions = b.positions()
    assert len(positions) == 1
    assert positions[0].quantity == 1.0


def test_position_averages_on_add() -> None:
    """Buy 1 @ 100, then buy 1 @ 200 → position 2 @ avg 150."""
    b = PaperBroker(
        get_price=lambda _: 100.0, slippage_bps=0.0, commission_bps=0.0, clock_ms=lambda: 0
    )
    b.place(Order("c1", "BTC/USDT", "buy", 1.0))
    b._get_price = lambda _: 200.0  # type: ignore[method-assign]
    b.place(Order("c2", "BTC/USDT", "buy", 1.0))

    pos = b.positions()[0]
    assert pos.quantity == 2.0
    assert pos.avg_entry_price == 150.0


def test_position_closes_to_zero() -> None:
    b = _broker(price=100.0, slippage=0.0, commission=0.0)
    b.place(Order("c1", "BTC/USDT", "buy", 1.0))
    b.place(Order("c2", "BTC/USDT", "sell", 1.0))
    assert b.positions() == []


def test_partial_close_preserves_avg_entry() -> None:
    b = _broker(price=100.0, slippage=0.0, commission=0.0)
    b.place(Order("c1", "BTC/USDT", "buy", 2.0))
    b.place(Order("c2", "BTC/USDT", "sell", 1.0))
    pos = b.positions()[0]
    assert pos.quantity == 1.0
    assert pos.avg_entry_price == 100.0


def test_restore_from_fills_rebuilds_positions_and_coid_cache() -> None:
    """Reopening a paper-broker after a restart must reconstruct positions
    + COID idempotency cache from the audit log, otherwise reconcile fails:
    log says 'have 1 BTC' while broker says 'have nothing' (real bug 2026-05-02).
    """
    fills = [
        {"event_type": "order_filled", "client_order_id": "c1", "timestamp_ms": 1,
         "symbol": "BTC/USDT", "side": "buy", "quantity": 1.0, "price": 100.0},
        {"event_type": "order_filled", "client_order_id": "c2", "timestamp_ms": 2,
         "symbol": "ETH/USDT", "side": "buy", "quantity": 5.0, "price": 50.0},
        {"event_type": "signal", "symbol": "BTC/USDT"},
        {"event_type": "order_filled", "client_order_id": "c3", "timestamp_ms": 3,
         "symbol": "BTC/USDT", "side": "sell", "quantity": 1.0, "price": 110.0},
    ]
    b = _broker(slippage=0.0, commission=0.0)
    b.restore_from_fills(fills)
    pos = {p.symbol: p for p in b.positions()}
    assert "BTC/USDT" not in pos
    assert pos["ETH/USDT"].quantity == 5.0
    cached = b.place(Order("c2", "ETH/USDT", "buy", 999.0))
    assert cached.quantity == 5.0
    assert pos["ETH/USDT"].quantity == 5.0


def test_restore_from_fills_is_idempotent() -> None:
    """Calling restore twice must not duplicate the position."""
    fills = [
        {"event_type": "order_filled", "client_order_id": "c1", "timestamp_ms": 1,
         "symbol": "BTC/USDT", "side": "buy", "quantity": 2.0, "price": 100.0},
    ]
    b = _broker()
    b.restore_from_fills(fills)
    b.restore_from_fills(fills)
    assert b.positions()[0].quantity == 2.0


def test_restore_from_fills_skips_malformed_rows() -> None:
    """A corrupt log row should not crash the rebuild — skip it silently."""
    fills = [
        {"event_type": "order_filled", "client_order_id": "c1", "timestamp_ms": 1,
         "symbol": "BTC/USDT", "side": "buy", "quantity": 1.0, "price": 100.0},
        {"event_type": "order_filled", "client_order_id": "c-bad",
         "symbol": "ETH/USDT", "side": "buy", "quantity": "not-a-number", "price": 50.0},
    ]
    b = _broker()
    b.restore_from_fills(fills)
    assert len(b.positions()) == 1
    assert b.positions()[0].symbol == "BTC/USDT"
