"""Paper-mode broker — uses real prices for marks, simulates fills with slippage.

Never places a real order. Holds an in-memory position book + fill cache. The cache
is keyed on `client_order_id` so a retried `place()` with the same ID returns the
ORIGINAL fill — that is the I-3 idempotency contract for the paper path. Live brokers
enforce the same contract by rejecting duplicate IDs at their API.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from execution.broker import Broker, Fill, Order, OrderSide, Position  # noqa: F401


class PaperBroker(Broker):
    def __init__(
        self,
        *,
        get_price: Callable[[str], float],
        slippage_bps: float = 5.0,
        commission_bps: float = 10.0,
        clock_ms: Callable[[], int] | None = None,
    ):
        self._get_price = get_price
        self._slippage = slippage_bps / 10_000.0
        self._commission = commission_bps / 10_000.0
        self._clock = clock_ms or (lambda: int(time.time() * 1000))
        self._positions: dict[str, Position] = {}
        self._fills_by_coid: dict[str, Fill] = {}

    def place(
        self,
        order: Order,
        *,
        mark_price: float | None = None,
        timestamp_ms: int | None = None,
    ) -> Fill | None:
        if order.client_order_id in self._fills_by_coid:
            return self._fills_by_coid[order.client_order_id]

        mark = mark_price if mark_price is not None else self._get_price(order.symbol)
        if order.side == "buy":
            fill_price = mark * (1.0 + self._slippage)
        else:
            fill_price = mark * (1.0 - self._slippage)
        fee = fill_price * order.quantity * self._commission

        fill = Fill(
            client_order_id=order.client_order_id,
            timestamp_ms=timestamp_ms if timestamp_ms is not None else self._clock(),
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            fee=fee,
        )
        self._fills_by_coid[order.client_order_id] = fill
        self._update_position(fill)
        return fill

    def cancel(self, client_order_id: str) -> bool:
        return False

    def positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.quantity != 0]

    def open_orders(self) -> list[Order]:
        return []

    def _update_position(self, fill: Fill) -> None:
        cur = self._positions.get(fill.symbol)
        if cur is None:
            qty = fill.quantity if fill.side == "buy" else -fill.quantity
            self._positions[fill.symbol] = Position(
                symbol=fill.symbol,
                quantity=qty,
                avg_entry_price=fill.price,
            )
            return

        signed_qty = fill.quantity if fill.side == "buy" else -fill.quantity
        new_qty = cur.quantity + signed_qty

        if (cur.quantity > 0 and signed_qty > 0) or (cur.quantity < 0 and signed_qty < 0):
            new_avg = (
                cur.avg_entry_price * abs(cur.quantity) + fill.price * abs(signed_qty)
            ) / abs(new_qty)
            self._positions[fill.symbol] = Position(fill.symbol, new_qty, new_avg)
        elif new_qty == 0 or (cur.quantity > 0) != (new_qty > 0):
            if new_qty == 0:
                self._positions.pop(fill.symbol, None)
            else:
                self._positions[fill.symbol] = Position(fill.symbol, new_qty, fill.price)
        else:
            self._positions[fill.symbol] = Position(fill.symbol, new_qty, cur.avg_entry_price)
