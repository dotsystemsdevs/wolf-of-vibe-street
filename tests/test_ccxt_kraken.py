"""Tests for execution.ccxt_kraken.KrakenBroker.

Skeleton-stage tests: focus on the safety interlocks, idempotency, and the
CCXT-shape ↔ our-types mapping helpers. Real network calls land in the next
session under tests/integration/ (skipped without KRAKEN_API_KEY).

3-test rule per surface: gate enforcement, dry-run, real-path with fake CCXT.
"""

from __future__ import annotations

from typing import Any

import pytest

from execution.broker import Order, make_client_order_id
from execution.ccxt_kraken import (
    KrakenBroker,
    _ccxt_balance_to_positions,
    _ccxt_orders_to_open_orders,
    _ccxt_response_to_fill,
    _coid_to_userref,
)


class _FakeCCXT:
    """Minimal CCXT stand-in. Records every call; returns canned responses."""

    def __init__(
        self,
        order_response: dict[str, Any] | None = None,
        balance: dict[str, Any] | None = None,
        open_orders: list[dict[str, Any]] | None = None,
    ) -> None:
        self.order_response = order_response or {}
        self.balance = balance or {}
        self.open_orders_resp = open_orders or []
        self.create_order_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[str] = []

    def create_order(self, symbol, type, side, amount, price=None, params=None) -> dict[str, Any]:  # noqa: A002
        self.create_order_calls.append(
            {
                "symbol": symbol,
                "type": type,
                "side": side,
                "amount": amount,
                "price": price,
                "params": params or {},
            }
        )
        return self.order_response

    def cancel_order(self, id, symbol=None) -> dict[str, Any]:  # noqa: A002
        self.cancel_calls.append(id)
        return {"id": id, "status": "canceled"}

    def fetch_balance(self, params=None) -> dict[str, Any]:
        return self.balance

    def fetch_open_orders(self, symbol=None, since=None, limit=None) -> list[dict[str, Any]]:
        return self.open_orders_resp


# --- Safety interlock: LIVE_TRADING gate ----------------------------------------


def test_failure_constructor_raises_without_live_trading_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No LIVE_TRADING flag → constructor refuses, even with valid keys."""
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    with pytest.raises(RuntimeError, match="LIVE_TRADING"):
        KrakenBroker(api_key="k", api_secret="s", exchange=_FakeCCXT())


def test_failure_constructor_raises_without_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """LIVE_TRADING set, but no key/secret and no exchange → ValueError."""
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    with pytest.raises(ValueError, match="KRAKEN_API_KEY"):
        KrakenBroker()


def test_failure_dry_run_still_requires_live_trading_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dry_run is NOT an escape hatch — env flag still required (defense in depth)."""
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    with pytest.raises(RuntimeError, match="LIVE_TRADING"):
        KrakenBroker(api_key="k", api_secret="s", exchange=_FakeCCXT(), dry_run=True)


# --- dry_run path ---------------------------------------------------------------


def test_expected_dry_run_synthesizes_fill_without_calling_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIVE_TRADING", "true")
    fake = _FakeCCXT()
    broker = KrakenBroker(api_key="k", api_secret="s", exchange=fake, dry_run=True)

    coid = make_client_order_id("test", "sig1")
    order = Order(client_order_id=coid, symbol="BTC/USDT", side="buy", quantity=0.01)
    fill = broker.place(order, mark_price=50_000.0, timestamp_ms=1)

    assert fill is not None
    assert fill.price == 50_000.0
    assert fill.quantity == 0.01
    assert fill.fee == 0.0
    assert fake.create_order_calls == []  # never called the exchange


def test_edge_dry_run_idempotent_on_same_coid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_TRADING", "true")
    broker = KrakenBroker(api_key="k", api_secret="s", exchange=_FakeCCXT(), dry_run=True)
    coid = make_client_order_id("test", "sig1")
    order = Order(client_order_id=coid, symbol="BTC/USDT", side="buy", quantity=0.01)
    f1 = broker.place(order, mark_price=50_000.0, timestamp_ms=1)
    f2 = broker.place(order, mark_price=99_999.0, timestamp_ms=2)
    assert f1 is f2  # cached, second call returns first fill
    assert f1.price == 50_000.0


def test_failure_dry_run_requires_mark_price(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_TRADING", "true")
    broker = KrakenBroker(api_key="k", api_secret="s", exchange=_FakeCCXT(), dry_run=True)
    coid = make_client_order_id("test", "sig1")
    order = Order(client_order_id=coid, symbol="BTC/USDT", side="buy", quantity=0.01)
    with pytest.raises(ValueError, match="mark_price"):
        broker.place(order, mark_price=None, timestamp_ms=1)


# --- Real-path with fake CCXT (idempotency, userref) ----------------------------


def test_expected_place_passes_userref_to_ccxt(monkeypatch: pytest.MonkeyPatch) -> None:
    """userref derived from coid must be in params — Kraken's idempotency hook."""
    monkeypatch.setenv("LIVE_TRADING", "true")
    fake = _FakeCCXT(
        order_response={
            "id": "kraken-id-1",
            "filled": 0.01,
            "average": 50_100.0,
            "fee": {"cost": 0.5},
            "timestamp": 1234,
        }
    )
    broker = KrakenBroker(api_key="k", api_secret="s", exchange=fake)
    coid = make_client_order_id("test", "sig1")
    order = Order(client_order_id=coid, symbol="BTC/USDT", side="buy", quantity=0.01)
    fill = broker.place(order, timestamp_ms=1)

    assert fill is not None
    assert fill.price == 50_100.0
    assert fill.quantity == 0.01
    assert fill.fee == 0.5
    assert len(fake.create_order_calls) == 1
    call = fake.create_order_calls[0]
    assert call["symbol"] == "BTC/USDT"
    assert call["side"] == "buy"
    assert call["amount"] == 0.01
    assert "userref" in call["params"]
    assert call["params"]["userref"] == _coid_to_userref(coid)


def test_edge_place_returns_none_when_order_did_not_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resting limit on the book: filled=0 → return None, executor decides what to do."""
    monkeypatch.setenv("LIVE_TRADING", "true")
    fake = _FakeCCXT(order_response={"id": "x", "filled": 0.0, "average": None})
    broker = KrakenBroker(api_key="k", api_secret="s", exchange=fake)
    order = Order(
        client_order_id=make_client_order_id("t", "s"),
        symbol="BTC/USDT",
        side="buy",
        quantity=0.01,
    )
    assert broker.place(order, timestamp_ms=1) is None


def test_edge_userref_fits_in_signed_32bit() -> None:
    """Kraken userref must be 0..2^31-1 for the various 32-char hex coids we generate."""
    for sig in ("sig1", "sig999999", "abc-def-123", "x" * 64):
        coid = make_client_order_id("strategy", sig)
        u = _coid_to_userref(coid)
        assert 0 <= u <= 0x7FFFFFFF


# --- Pure-helper coverage -------------------------------------------------------


def test_balance_to_positions_drops_quote_and_zero() -> None:
    balance = {
        "BTC": {"free": 0.1, "used": 0, "total": 0.1},
        "ETH": {"free": 0, "used": 0, "total": 0.0},
        "USDT": {"free": 5000, "used": 0, "total": 5000},
        "free": {},  # CCXT-shape junk we should skip
    }
    positions = _ccxt_balance_to_positions(balance)
    assert len(positions) == 1
    assert positions[0].symbol == "BTC/USDT"
    assert positions[0].quantity == 0.1


def test_open_orders_parsing_skips_malformed() -> None:
    raw = [
        {
            "id": "1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "amount": 0.1,
            "type": "limit",
            "price": 50_000,
        },
        {
            "id": "2",
            "symbol": "ETH/USDT",
            "side": "junk",
            "amount": 1,
            "type": "market",
        },  # bad side
        {
            "id": "3",
            "symbol": "SOL/USDT",
            "side": "sell",
            "amount": 0,
            "type": "market",
        },  # zero amt
        "garbage",  # not even a dict
    ]
    orders = _ccxt_orders_to_open_orders(raw)
    assert len(orders) == 1
    assert orders[0].symbol == "BTC/USDT"
    assert orders[0].price == 50_000


def test_response_to_fill_returns_none_on_zero_filled() -> None:
    order = Order(
        client_order_id=make_client_order_id("t", "s"),
        symbol="BTC/USDT",
        side="buy",
        quantity=1.0,
    )
    assert _ccxt_response_to_fill({"filled": 0, "average": None}, order, fallback_ts=1) is None
    assert (
        _ccxt_response_to_fill({"filled": 1, "average": None}, order, fallback_ts=1) is None
    )  # no avg


# --- cancel() ---


def test_expected_cancel_resolves_coid_via_userref(monkeypatch: pytest.MonkeyPatch) -> None:
    """cancel(coid) must fetch open orders, match userref, and call cancel_order on the match."""
    monkeypatch.setenv("LIVE_TRADING", "true")
    coid = make_client_order_id("test", "sig1")
    expected_userref = _coid_to_userref(coid)
    fake = _FakeCCXT(
        open_orders=[
            {
                "id": "kraken-order-99",
                "symbol": "BTC/USDT",
                "side": "buy",
                "amount": 0.01,
                "type": "limit",
                "price": 50000,
                # CCXT often surfaces userref via params.userref or info.userref
                "params": {"userref": expected_userref},
            },
            {
                # Unrelated order — different userref, must not be cancelled.
                "id": "kraken-order-100",
                "symbol": "BTC/USDT",
                "side": "sell",
                "amount": 0.01,
                "type": "limit",
                "price": 60000,
                "params": {"userref": expected_userref + 1},
            },
        ]
    )
    broker = KrakenBroker(api_key="k", api_secret="s", exchange=fake)
    assert broker.cancel(coid) is True
    assert fake.cancel_calls == ["kraken-order-99"]


def test_edge_cancel_returns_false_when_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """No open order with matching userref → return False, no cancel_order call."""
    monkeypatch.setenv("LIVE_TRADING", "true")
    coid = make_client_order_id("test", "sig1")
    fake = _FakeCCXT(open_orders=[])
    broker = KrakenBroker(api_key="k", api_secret="s", exchange=fake)
    assert broker.cancel(coid) is False
    assert fake.cancel_calls == []


def test_edge_cancel_falls_back_to_info_userref(monkeypatch: pytest.MonkeyPatch) -> None:
    """Some CCXT versions surface userref under info.userref, not params.userref."""
    monkeypatch.setenv("LIVE_TRADING", "true")
    coid = make_client_order_id("test", "sig1")
    expected_userref = _coid_to_userref(coid)
    fake = _FakeCCXT(
        open_orders=[
            {
                "id": "kraken-order-50",
                "symbol": "BTC/USDT",
                "side": "buy",
                "amount": 0.01,
                "type": "limit",
                "price": 50000,
                "info": {"userref": str(expected_userref)},  # string, also acceptable
            },
        ]
    )
    broker = KrakenBroker(api_key="k", api_secret="s", exchange=fake)
    assert broker.cancel(coid) is True
    assert fake.cancel_calls == ["kraken-order-50"]


def test_dry_run_cancel_drops_cached_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    """In dry_run, cancel(coid) must clear the cached fill so a re-place creates a new one."""
    monkeypatch.setenv("LIVE_TRADING", "true")
    broker = KrakenBroker(api_key="k", api_secret="s", exchange=_FakeCCXT(), dry_run=True)
    coid = make_client_order_id("test", "sig1")
    order = Order(client_order_id=coid, symbol="BTC/USDT", side="buy", quantity=0.01)
    f1 = broker.place(order, mark_price=50_000.0, timestamp_ms=1)
    assert broker.cancel(coid) is True
    # Cancelling again returns False (already cleared).
    assert broker.cancel(coid) is False
    # Re-place creates a fresh fill (different price proves it's not cached).
    f2 = broker.place(order, mark_price=51_000.0, timestamp_ms=2)
    assert f1 is not f2
    assert f2.price == 51_000.0
