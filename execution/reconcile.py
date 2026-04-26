"""Reconcile-on-startup — defense against broker/local state divergence (P-11).

When the bot starts, the local picture of the world (decision log → derived
positions + open coids) MUST match what the broker says. If it doesn't, we
have one of:

  - A position opened by another process / a manual trade in the UI we didn't
    log → the bot would happily layer a second position on top, doubling risk.
  - A position the log thinks is open but the broker says is gone → the bot
    would never see the exit, leaving the local state stuck "in trade" forever.
  - An open order on the exchange that we don't have a coid for → likely a
    stale order from a previous run. Could block new entries silently.

Pure module: takes a broker + a list of decision-log rows + a tolerance, returns
a `ReconcileResult` with structured mismatches. The caller (live_loop's
build_from_env) decides what to do with mismatches — paper just logs them; live
halts new entries until manually resolved.

Tolerance: float quantities can drift by epsilon-level amounts due to fee
deductions, rounding at the exchange. `qty_tolerance=1e-6` matches what we
already use in `ui.views.open_positions` and is well below any tradeable size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from execution.broker import Broker, Position
from ui.views import open_positions as positions_from_log


@dataclass(frozen=True, slots=True)
class PositionMismatch:
    """A symbol where broker quantity ≠ log-derived quantity beyond tolerance."""

    symbol: str
    broker_qty: float
    log_qty: float

    @property
    def delta(self) -> float:
        return self.broker_qty - self.log_qty


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """Outcome of a reconcile pass. `is_clean` is the only thing the caller acts on."""

    broker_positions: list[Position]
    log_positions: list[dict[str, Any]]
    open_orders_count: int
    mismatches: list[PositionMismatch] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """True only if no mismatches AND no orphaned open orders."""
        return not self.mismatches and self.open_orders_count == 0

    def summary(self) -> str:
        """One-line human-readable summary for log + Telegram."""
        if self.is_clean:
            return (
                f"reconcile OK · {len(self.broker_positions)} broker positions · "
                f"{len(self.log_positions)} log positions"
            )
        bits: list[str] = []
        if self.mismatches:
            sym_list = ", ".join(
                f"{m.symbol}(broker={m.broker_qty:g} log={m.log_qty:g})" for m in self.mismatches
            )
            bits.append(f"{len(self.mismatches)} qty mismatch: {sym_list}")
        if self.open_orders_count:
            bits.append(f"{self.open_orders_count} orphan open orders on broker")
        return "reconcile FAILED · " + " · ".join(bits)


def reconcile(
    broker: Broker,
    decision_rows: list[dict[str, Any]],
    *,
    qty_tolerance: float = 1e-6,
) -> ReconcileResult:
    """Compare broker's view of positions/orders against the decision-log-derived view.

    Pulls broker.positions() and broker.open_orders() exactly once each. Walks
    the union of symbols from both sides — a position that exists on only one
    side counts as a mismatch with the missing side's qty = 0.
    """
    broker_pos: list[Position] = list(broker.positions())
    log_pos: list[dict[str, Any]] = positions_from_log(decision_rows)
    broker_qty_by_sym: dict[str, float] = {p.symbol: p.quantity for p in broker_pos}
    log_qty_by_sym: dict[str, float] = {p["symbol"]: float(p["qty"]) for p in log_pos}

    mismatches: list[PositionMismatch] = []
    for sym in broker_qty_by_sym.keys() | log_qty_by_sym.keys():
        b_qty = broker_qty_by_sym.get(sym, 0.0)
        l_qty = log_qty_by_sym.get(sym, 0.0)
        if abs(b_qty - l_qty) > qty_tolerance:
            mismatches.append(PositionMismatch(symbol=sym, broker_qty=b_qty, log_qty=l_qty))

    # Open orders: any order on the exchange means the broker thinks something
    # is pending. PaperBroker always returns []; live brokers may have stale
    # resting orders. We count them; the executor's policy is to halt new
    # entries until the operator clears them via the UI cancel button (Phase 3).
    open_orders_count = len(broker.open_orders())

    return ReconcileResult(
        broker_positions=broker_pos,
        log_positions=log_pos,
        open_orders_count=open_orders_count,
        mismatches=sorted(mismatches, key=lambda m: m.symbol),
    )
