"""Kraken live broker via CCXT.

The first real-money adapter. Implements the same `Broker` Protocol as PaperBroker
so the executor + decision log keep working without changes — only the wiring in
`workers.live_loop.build_from_env` decides which one is constructed.

Safety interlocks (CLAUDE.md §3.2):
  1. Constructor calls `assert_live_trading_enabled()` — raises if LIVE_TRADING
     env var is not exactly "true". This applies even in dry_run mode, on the
     theory that anyone bypassing the env flag for a "test run" is exactly the
     mode of failure the flag exists to prevent.
  2. `dry_run=True` short-circuits `place()` to a synthetic Fill at mark_price
     (no API call). Useful for the per-session human-gate flow before the
     operator confirms full live mode.
  3. Every order carries a deterministic `client_order_id` mapped to Kraken's
     32-bit `userref` field. Kraken rejects duplicate userref+symbol on the same
     account, giving us defense-in-depth on top of our own coid cache.

This module never imports `ccxt` at module load — only inside the constructor —
so unit tests can run on a machine without ccxt installed by passing a fake
exchange. The CCXTExchange Protocol below documents the surface we depend on.
"""

from __future__ import annotations

import os
import time
from typing import Any, Protocol

from execution.broker import Broker, Fill, Order, Position
from risk.live_gate import assert_live_trading_enabled


class CCXTExchange(Protocol):
    """The slice of ccxt's exchange we actually touch. Lets tests inject fakes."""

    def create_order(
        self,
        symbol: str,
        type: str,  # noqa: A002 — CCXT param name
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def cancel_order(
        self,
        id: str,
        symbol: str | None = None,  # noqa: A002 — CCXT param name
    ) -> dict[str, Any]: ...

    def fetch_balance(self, params: dict[str, Any] | None = None) -> dict[str, Any]: ...

    def fetch_open_orders(
        self, symbol: str | None = None, since: int | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]: ...


def _coid_to_userref(client_order_id: str) -> int:
    """Map our 32-char hex coid to a 32-bit positive int for Kraken's `userref`.

    Kraken's `userref` is a signed 32-bit integer. We take the first 8 hex chars
    of the coid (already SHA-256 derived), parse to int, and mask off the high
    bit so it stays positive — Kraken accepts 0..2^31-1.
    """
    return int(client_order_id[:8], 16) & 0x7FFFFFFF


class KrakenBroker(Broker):
    """Real-money Kraken adapter.

    Constructor args:
      api_key, api_secret: Kraken API credentials. If None, read from env
        KRAKEN_API_KEY / KRAKEN_API_SECRET. Missing → ValueError.
      dry_run: when True, every place() returns a synthetic Fill without an
        actual API call. Constructor still requires LIVE_TRADING=true.
      exchange: optional pre-built CCXTExchange (used by tests). If None, we
        import ccxt and build a kraken client from the keys.
      clock_ms: epoch-ms clock for fill timestamps. Defaults to time.time().

    Idempotency: `place()` includes `userref=_coid_to_userref(coid)` in the
    Kraken params. A retried place with the same coid will be rejected by
    Kraken with "EOrder:Duplicate userref" — the executor treats that as a
    no-op and re-fetches the original fill on the next reconcile pass.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        dry_run: bool = False,
        exchange: CCXTExchange | None = None,
        clock_ms: Any = None,
    ):
        # Hard interlock — even dry_run requires the env flag, by design.
        assert_live_trading_enabled()

        self._dry_run = dry_run
        self._clock = clock_ms or (lambda: int(time.time() * 1000))
        self._fills_by_coid: dict[str, Fill] = {}

        if exchange is not None:
            self._exchange: CCXTExchange = exchange
        else:
            key = api_key or os.environ.get("KRAKEN_API_KEY", "").strip()
            secret = api_secret or os.environ.get("KRAKEN_API_SECRET", "").strip()
            if not key or not secret:
                raise ValueError(
                    "KrakenBroker requires KRAKEN_API_KEY and KRAKEN_API_SECRET "
                    "(in env or constructor args)."
                )
            try:
                import ccxt  # noqa: PLC0415  — defer; ccxt is heavy
            except ImportError as e:  # pragma: no cover — env always has ccxt
                raise RuntimeError(
                    "ccxt not installed. Run `uv add ccxt` to enable live trading."
                ) from e
            self._exchange = ccxt.kraken({"apiKey": key, "secret": secret, "enableRateLimit": True})

    # ----- Broker Protocol --------------------------------------------------

    def place(
        self,
        order: Order,
        *,
        mark_price: float | None = None,
        timestamp_ms: int | None = None,
    ) -> Fill | None:
        # Coid cache prevents same-process double-fill on retry.
        if order.client_order_id in self._fills_by_coid:
            return self._fills_by_coid[order.client_order_id]

        ts = timestamp_ms if timestamp_ms is not None else self._clock()

        if self._dry_run:
            # Dry-run: synthesize a fill at mark_price, do not call the API.
            # Useful for the human-gate workflow: live broker is constructed
            # but no actual orders flow until the operator un-checks dry_run.
            if mark_price is None:
                raise ValueError(
                    "dry_run KrakenBroker.place requires mark_price (no market lookup yet)"
                )
            fill = Fill(
                client_order_id=order.client_order_id,
                timestamp_ms=ts,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=float(mark_price),
                fee=0.0,
            )
            self._fills_by_coid[order.client_order_id] = fill
            return fill

        params = {"userref": _coid_to_userref(order.client_order_id)}
        try:
            response = self._exchange.create_order(
                symbol=order.symbol,
                type=order.order_type,
                side=order.side,
                amount=order.quantity,
                price=order.price,
                params=params,
            )
        except Exception:  # noqa: BLE001 — broker errors are logged by caller
            # Caller (Executor) decides what to do — typically log order_rejected
            # and let the loop retry on the next bar. We deliberately don't swallow
            # silently here; the exception propagates.
            raise

        fill = _ccxt_response_to_fill(response, order, fallback_ts=ts)
        if fill is not None:
            self._fills_by_coid[order.client_order_id] = fill
        return fill

    def cancel(self, client_order_id: str) -> bool:
        """Cancel an open order by our coid. True if cancelled, False otherwise.

        Kraken's API needs the exchange-side order id, not our coid. We map by
        looking at every open order's `userref` (set by us in `place()`) and
        matching `_coid_to_userref(client_order_id)`. If multiple orders match
        the same userref (shouldn't happen — userref is per-coid), we cancel
        all of them and return True if any cancel succeeds.

        Dry-run: drop the cached fill so a re-place() with the same coid
        creates a fresh synthetic fill instead of returning the cached one.
        """
        if self._dry_run:
            existed = client_order_id in self._fills_by_coid
            self._fills_by_coid.pop(client_order_id, None)
            return existed

        target_userref = _coid_to_userref(client_order_id)
        try:
            raw = self._exchange.fetch_open_orders()
        except Exception:  # noqa: BLE001
            return False

        cancelled_any = False
        for order_dict in raw:
            if not isinstance(order_dict, dict):
                continue
            # CCXT exposes Kraken's userref under params.userref or info.userref.
            params = order_dict.get("params") or {}
            info = order_dict.get("info") or {}
            userref = params.get("userref") if isinstance(params, dict) else None
            if userref is None and isinstance(info, dict):
                userref = info.get("userref")
            try:
                userref_int = int(userref) if userref is not None else None
            except (TypeError, ValueError):
                userref_int = None
            if userref_int != target_userref:
                continue

            exchange_id = order_dict.get("id")
            symbol = order_dict.get("symbol")
            if not exchange_id:
                continue
            try:
                self._exchange.cancel_order(str(exchange_id), symbol)
                cancelled_any = True
                # Drop our coid cache so a future place() with the same coid
                # is allowed to retry (post-cancel, the order is gone).
                self._fills_by_coid.pop(client_order_id, None)
            except Exception:  # noqa: BLE001
                # One cancel failed; keep trying others. Final return reflects
                # whether *any* cancel succeeded.
                continue
        return cancelled_any

    def positions(self) -> list[Position]:
        if self._dry_run:
            return []
        balance = self._exchange.fetch_balance()
        return _ccxt_balance_to_positions(balance)

    def open_orders(self) -> list[Order]:
        if self._dry_run:
            return []
        raw = self._exchange.fetch_open_orders()
        return _ccxt_orders_to_open_orders(raw)


# --- Pure helpers (no CCXT import; easy to unit-test) -----------------------------


def _ccxt_response_to_fill(
    response: dict[str, Any], order: Order, *, fallback_ts: int
) -> Fill | None:
    """Map a CCXT create_order response to our Fill type. Returns None if the
    order didn't actually fill (e.g., resting limit on the book)."""
    filled = float(response.get("filled") or 0.0)
    if filled <= 0:
        return None
    avg = response.get("average") or response.get("price")
    if avg is None:
        return None
    fee_obj = response.get("fee") or {}
    fee_cost = float(fee_obj.get("cost") or 0.0) if isinstance(fee_obj, dict) else 0.0
    ts = int(response.get("timestamp") or fallback_ts)
    return Fill(
        client_order_id=order.client_order_id,
        timestamp_ms=ts,
        symbol=order.symbol,
        side=order.side,
        quantity=filled,
        price=float(avg),
        fee=fee_cost,
    )


def _ccxt_balance_to_positions(balance: dict[str, Any]) -> list[Position]:
    """Extract long-only positions from a CCXT fetch_balance() result.

    CCXT shape: {'BTC': {'free': 0.1, 'used': 0, 'total': 0.1}, 'USDT': {...}, ...}.
    We treat any non-USDT asset with total > 0 as an open position. avg_entry_price
    is *not* known from balance alone — set to 0.0 here; the executor's local
    open-position state has the true entry. Reconcile-on-startup (next session)
    will cross-check qty against decision-log derived state.
    """
    positions: list[Position] = []
    for asset, info in balance.items():
        if not isinstance(info, dict):
            continue
        if asset in {"USDT", "USD", "EUR", "info", "free", "used", "total"}:
            continue
        total = float(info.get("total") or 0.0)
        if total > 0:
            # CCXT usually exposes assets like 'BTC' (not 'BTC/USDT'). Map to
            # our convention by appending /USDT — fine for spot crypto on
            # USDT-quoted pairs; revisit when we add multi-quote support.
            positions.append(Position(symbol=f"{asset}/USDT", quantity=total, avg_entry_price=0.0))
    return positions


def _ccxt_orders_to_open_orders(raw: list[dict[str, Any]]) -> list[Order]:
    """Best-effort recreate Order objects from CCXT open-orders list.

    The exchange-side `userref` is what we use as our coid surrogate in logs;
    we don't have the original 32-char coid back from Kraken (just the int).
    For now we synthesize a coid prefix from the exchange order id — good
    enough for the dashboard's "open orders" view; reconcile-on-startup will
    do proper coid round-trip.
    """
    out: list[Order] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        symbol = r.get("symbol")
        side = r.get("side")
        amount = float(r.get("amount") or 0.0)
        if not symbol or side not in {"buy", "sell"} or amount <= 0:
            continue
        out.append(
            Order(
                client_order_id=str(r.get("id") or "unknown"),
                symbol=str(symbol),
                side=side,  # type: ignore[arg-type]
                quantity=amount,
                order_type=str(r.get("type") or "market"),  # type: ignore[arg-type]
                price=float(r["price"]) if r.get("price") is not None else None,
            )
        )
    return out
