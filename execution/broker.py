"""Broker interface (Protocol) + shared order/fill/position types.

Per I-1: only modules in `execution/` may import broker SDKs for *order placement*.
Concrete adapters (paper or live) implement `Broker` and are the only objects that
ever touch a broker SDK's authenticated endpoints.

Per I-3: every order carries an idempotent `client_order_id` derived deterministically
from `(strategy_id, signal_id, attempt)` — `make_client_order_id` is the helper.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal, Protocol

OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]


@dataclass(frozen=True, slots=True)
class Order:
    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = "market"
    price: float | None = None  # required for limit, ignored for market

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"order quantity must be > 0, got {self.quantity}")
        if self.order_type == "limit" and self.price is None:
            raise ValueError("limit order requires price")


@dataclass(frozen=True, slots=True)
class Fill:
    client_order_id: str
    timestamp_ms: int
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    fee: float

    @property
    def notional(self) -> float:
        return self.quantity * self.price


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    quantity: float
    avg_entry_price: float

    @property
    def notional(self) -> float:
        return abs(self.quantity) * self.avg_entry_price


class Broker(Protocol):
    """The contract the executor uses. Paper + live adapters both implement this.

    `mark_price` lets the executor say "fill near this level" — used for stop/target
    exits where the trigger price is known. Paper adapters fill at `mark_price` (with
    slippage). Live adapters can ignore it and rely on the underlying order type.
    """

    def place(
        self,
        order: Order,
        *,
        mark_price: float | None = None,
        timestamp_ms: int | None = None,
    ) -> Fill | None: ...
    def cancel(self, client_order_id: str) -> bool: ...
    def positions(self) -> list[Position]: ...
    def open_orders(self) -> list[Order]: ...


def make_client_order_id(strategy_id: str, signal_id: str, attempt: int = 0) -> str:
    """Deterministic ID for I-3 idempotency.

    Same (strategy_id, signal_id, attempt) → same ID. A retried place() with the same
    ID must not double-fill — paper broker enforces this by caching by coid; live
    brokers (Kraken, Binance) all reject duplicate client order IDs.

    Returns first 32 hex chars of SHA-256 (fits Binance/Kraken length limits).
    """
    if not strategy_id or not signal_id:
        raise ValueError("strategy_id and signal_id must both be non-empty")
    raw = f"{strategy_id}:{signal_id}:{attempt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
