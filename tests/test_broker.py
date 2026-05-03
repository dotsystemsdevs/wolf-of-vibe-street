"""Tests for execution.broker types + make_client_order_id."""

from __future__ import annotations

import pytest

from execution.broker import Order, make_client_order_id


def test_expected_market_order_constructs() -> None:
    o = Order(client_order_id="x", symbol="BTC/USDT", side="buy", quantity=1.0)
    assert o.order_type == "market"


def test_failure_zero_quantity_raises() -> None:
    with pytest.raises(ValueError, match="quantity"):
        Order(client_order_id="x", symbol="BTC/USDT", side="buy", quantity=0.0)


def test_failure_limit_without_price_raises() -> None:
    with pytest.raises(ValueError, match="limit"):
        Order(client_order_id="x", symbol="BTC/USDT", side="buy", quantity=1.0, order_type="limit")


def test_make_client_order_id_deterministic() -> None:
    """I-3: same inputs → same ID. That is the idempotency contract."""
    a = make_client_order_id("baseline_ema_cross", "BTC/USDT", "1234567890")
    b = make_client_order_id("baseline_ema_cross", "BTC/USDT", "1234567890")
    assert a == b
    assert len(a) == 32


def test_make_client_order_id_different_attempts_distinct() -> None:
    a = make_client_order_id("s", "BTC/USDT", "sig", attempt=0)
    b = make_client_order_id("s", "BTC/USDT", "sig", attempt=1)
    assert a != b


def test_make_client_order_id_distinct_per_symbol() -> None:
    """Multi-symbol: same bar timestamp on two symbols must NOT collide. (Real bug:
    on a multi-symbol run both BUYs at 16:00 produced the same COID and the paper
    broker's idempotency cache returned the first symbol's fill for the second.)"""
    a = make_client_order_id("s", "BTC/USDT", "sig")
    b = make_client_order_id("s", "ETH/USDT", "sig")
    assert a != b


def test_make_client_order_id_rejects_empty() -> None:
    with pytest.raises(ValueError):
        make_client_order_id("", "BTC/USDT", "sig")
    with pytest.raises(ValueError):
        make_client_order_id("s", "", "sig")
    with pytest.raises(ValueError):
        make_client_order_id("s", "BTC/USDT", "")
