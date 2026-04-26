"""Tests for execution.reconcile — broker/log divergence detection (P-11).

3-test rule per CLAUDE.md §3.1:
  - expected: clean state on both sides → is_clean=True
  - edge: position on broker not in log / position in log not on broker / qty drift within tolerance
  - failure: orphaned open orders count
"""

from __future__ import annotations

from execution.broker import Broker, Order, Position
from execution.reconcile import PositionMismatch, ReconcileResult, reconcile


class _StubBroker(Broker):
    """Returns canned positions + open_orders. Doesn't model fills."""

    def __init__(
        self,
        positions: list[Position] | None = None,
        open_orders: list[Order] | None = None,
    ) -> None:
        self._positions = positions or []
        self._open_orders = open_orders or []

    def place(self, order, *, mark_price=None, timestamp_ms=None):  # noqa: D401
        return None

    def cancel(self, client_order_id):
        return False

    def positions(self):
        return list(self._positions)

    def open_orders(self):
        return list(self._open_orders)


def _fill_row(symbol: str, side: str, qty: float, price: float, ts: int) -> dict:
    """Minimal decision-log row shape used by ui.views.open_positions."""
    return {
        "id": ts,
        "timestamp_ms": ts,
        "event_type": "order_filled",
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "price": price,
        "rationale": "",
        "metadata_json": None,
    }


def test_expected_clean_state_when_both_sides_match() -> None:
    """Happy path: broker and log agree on positions, no open orders."""
    broker = _StubBroker(
        positions=[Position(symbol="BTC/USDT", quantity=0.1, avg_entry_price=50_000.0)],
    )
    rows = [_fill_row("BTC/USDT", "buy", 0.1, 50_000.0, ts=1)]
    result = reconcile(broker, rows)
    assert isinstance(result, ReconcileResult)
    assert result.is_clean
    assert result.mismatches == []
    assert "OK" in result.summary()


def test_edge_position_on_broker_not_in_log_is_mismatch() -> None:
    """Operator manually opened a position on the exchange — bot must halt in live mode."""
    broker = _StubBroker(
        positions=[Position(symbol="BTC/USDT", quantity=0.5, avg_entry_price=50_000.0)],
    )
    result = reconcile(broker, decision_rows=[])
    assert not result.is_clean
    assert len(result.mismatches) == 1
    m = result.mismatches[0]
    assert m.symbol == "BTC/USDT"
    assert m.broker_qty == 0.5
    assert m.log_qty == 0.0
    assert m.delta == 0.5
    assert "FAILED" in result.summary()


def test_edge_position_in_log_not_on_broker_is_mismatch() -> None:
    """Inverse: log thinks we're long but broker says no position. Bot would never see exit."""
    broker = _StubBroker(positions=[])
    rows = [_fill_row("ETH/USDT", "buy", 1.5, 3_000.0, ts=1)]
    result = reconcile(broker, rows)
    assert not result.is_clean
    assert len(result.mismatches) == 1
    assert result.mismatches[0].symbol == "ETH/USDT"
    assert result.mismatches[0].broker_qty == 0.0
    assert result.mismatches[0].log_qty == 1.5


def test_edge_quantity_drift_within_tolerance_is_clean() -> None:
    """Float epsilon from fee accounting must not trigger false mismatches."""
    broker = _StubBroker(
        positions=[Position(symbol="BTC/USDT", quantity=0.10000000001, avg_entry_price=50_000.0)],
    )
    rows = [_fill_row("BTC/USDT", "buy", 0.1, 50_000.0, ts=1)]
    result = reconcile(broker, rows, qty_tolerance=1e-6)
    assert result.is_clean


def test_edge_quantity_drift_beyond_tolerance_is_mismatch() -> None:
    """Drift > tolerance should be flagged."""
    broker = _StubBroker(
        positions=[Position(symbol="BTC/USDT", quantity=0.11, avg_entry_price=50_000.0)],
    )
    rows = [_fill_row("BTC/USDT", "buy", 0.1, 50_000.0, ts=1)]
    result = reconcile(broker, rows)
    assert not result.is_clean
    assert result.mismatches[0].symbol == "BTC/USDT"


def test_failure_orphan_open_orders_break_clean_state() -> None:
    """Even if positions match perfectly, any resting open order on broker is suspect."""
    broker = _StubBroker(
        open_orders=[
            Order(client_order_id="abc", symbol="BTC/USDT", side="buy", quantity=0.05),
        ],
    )
    result = reconcile(broker, decision_rows=[])
    assert not result.is_clean
    assert result.open_orders_count == 1
    assert "open orders" in result.summary()


def test_position_mismatch_dataclass_delta_is_directional() -> None:
    m_long = PositionMismatch(symbol="BTC/USDT", broker_qty=0.5, log_qty=0.1)
    assert m_long.delta == 0.4
    m_short = PositionMismatch(symbol="BTC/USDT", broker_qty=0.0, log_qty=0.5)
    assert m_short.delta == -0.5
