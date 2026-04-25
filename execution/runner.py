"""Bar-driven executor — wires signal → risk caps → broker → decision log.

One position at a time (Phase 1). Each call to `on_bar` processes one bar:
1. Mark-to-market the open position to bar close → updates equity.
2. If position open and stop/target hit intra-bar → exit at that level.
3. Apply the signal: buy (if flat) or sell (if long).

Every meaningful event emits a row to the decision log (I-6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from execution.broker import Broker, Order, make_client_order_id
from memory.decision_log import DecisionEvent, DecisionLog
from risk.caps import RiskCaps, RiskState, check_entry
from risk.sizing import position_size
from signals.types import Signal


@dataclass
class Bar:
    timestamp_ms: int
    high: float
    low: float
    close: float


def _utc_day(ts_ms: int) -> int:
    """UTC day-number (days since epoch)."""
    return ts_ms // 86_400_000


def _utc_iso_week(ts_ms: int) -> tuple[int, int]:
    """ISO (year, week) for the given timestamp — same key for all bars in a Mon-Sun span."""
    iso = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).isocalendar()
    return (iso.year, iso.week)


class Executor:
    """Single-symbol, single-position, long-only paper-mode runner."""

    def __init__(
        self,
        broker: Broker,
        log: DecisionLog,
        *,
        strategy_id: str,
        initial_cash: float,
        caps: RiskCaps | None = None,
        risk_pct: float = 0.005,
    ):
        self.broker = broker
        self.log = log
        self.strategy_id = strategy_id
        self.cash = initial_cash
        self.risk_pct = risk_pct
        self.caps = caps
        self.daily_high_water = initial_cash
        self.weekly_high_water = initial_cash
        self._current_day: int | None = None
        self._current_week: tuple[int, int] | None = None
        self._open_stop: float | None = None
        self._open_target: float | None = None
        self._open_signal_id: str | None = None

    def equity(self, mark_price_by_symbol: dict[str, float]) -> float:
        eq = self.cash
        for pos in self.broker.positions():
            mark = mark_price_by_symbol.get(pos.symbol, pos.avg_entry_price)
            eq += pos.quantity * mark
        return eq

    def on_bar(self, signal: Signal, bar: Bar) -> None:
        eq = self.equity({signal.symbol: bar.close})

        day = _utc_day(bar.timestamp_ms)
        week = _utc_iso_week(bar.timestamp_ms)
        if self._current_day is None or day != self._current_day:
            self.daily_high_water = eq
            self._current_day = day
        if self._current_week is None or week != self._current_week:
            self.weekly_high_water = eq
            self._current_week = week

        if eq > self.daily_high_water:
            self.daily_high_water = eq
        if eq > self.weekly_high_water:
            self.weekly_high_water = eq

        positions = self.broker.positions()
        open_pos = next((p for p in positions if p.symbol == signal.symbol), None)

        if open_pos is not None and self._open_stop is not None and self._open_target is not None:
            exit_reason: str | None = None
            exit_price: float | None = None
            if bar.low <= self._open_stop:
                exit_reason, exit_price = "stop_hit", self._open_stop
            elif bar.high >= self._open_target:
                exit_reason, exit_price = "target_hit", self._open_target
            if exit_reason is not None and exit_price is not None:
                self._exit(open_pos.symbol, open_pos.quantity, exit_price, exit_reason, bar)
                open_pos = None

        self.log.append(
            DecisionEvent(
                timestamp_ms=signal.timestamp_ms,
                event_type="signal",
                symbol=signal.symbol,
                side=signal.side,
                strategy_id=self.strategy_id,
                signal_id=str(signal.timestamp_ms),
                rationale=signal.rationale or None,
                metadata={"conviction": signal.conviction},
            )
        )

        if signal.side == "buy" and open_pos is None and signal.stop is not None:
            self._try_enter(signal, bar)
        elif signal.side == "sell" and open_pos is not None:
            self._exit(open_pos.symbol, open_pos.quantity, bar.close, "signal_exit", bar)

    def _try_enter(self, signal: Signal, bar: Bar) -> None:
        assert signal.stop is not None
        eq = self.equity({signal.symbol: bar.close})
        qty = position_size(eq, bar.close, signal.stop, risk_pct=self.risk_pct)
        if qty <= 0:
            return
        notional = qty * bar.close

        if self.caps is not None:
            state = RiskState(
                equity_now=eq,
                daily_high_water=self.daily_high_water,
                weekly_high_water=self.weekly_high_water,
                open_positions_count=len(self.broker.positions()),
                open_total_notional_usd=sum(p.notional for p in self.broker.positions()),
            )
            decision = check_entry(state, notional, self.caps)
            if not decision.allow:
                self.log.append(
                    DecisionEvent(
                        timestamp_ms=signal.timestamp_ms,
                        event_type="risk_block",
                        symbol=signal.symbol,
                        side=signal.side,
                        strategy_id=self.strategy_id,
                        signal_id=str(signal.timestamp_ms),
                        rationale=decision.reason,
                        notional=notional,
                    )
                )
                return

        coid = make_client_order_id(self.strategy_id, str(signal.timestamp_ms))
        order = Order(
            client_order_id=coid,
            symbol=signal.symbol,
            side="buy",
            quantity=qty,
            order_type="market",
        )
        self.log.append(
            DecisionEvent(
                timestamp_ms=signal.timestamp_ms,
                event_type="order_placed",
                symbol=signal.symbol,
                side="buy",
                strategy_id=self.strategy_id,
                signal_id=str(signal.timestamp_ms),
                client_order_id=coid,
                quantity=qty,
                price=bar.close,
                notional=notional,
            )
        )
        fill = self.broker.place(order, mark_price=bar.close)
        if fill is None:
            self.log.append(
                DecisionEvent(
                    timestamp_ms=signal.timestamp_ms,
                    event_type="order_rejected",
                    symbol=signal.symbol,
                    side="buy",
                    strategy_id=self.strategy_id,
                    signal_id=str(signal.timestamp_ms),
                    client_order_id=coid,
                    rationale="broker returned None",
                )
            )
            return
        cost = fill.price * fill.quantity + fill.fee
        self.cash -= cost
        self._open_stop = signal.stop
        self._open_target = signal.target
        self._open_signal_id = str(signal.timestamp_ms)
        self.log.append(
            DecisionEvent(
                timestamp_ms=fill.timestamp_ms,
                event_type="order_filled",
                symbol=fill.symbol,
                side="buy",
                strategy_id=self.strategy_id,
                signal_id=self._open_signal_id,
                client_order_id=coid,
                quantity=fill.quantity,
                price=fill.price,
                notional=fill.notional,
                slippage_bps=(fill.price / bar.close - 1.0) * 10_000.0,
            )
        )

    def _exit(self, symbol: str, qty: float, price: float, reason: str, bar: Bar) -> None:
        if qty <= 0:
            return
        coid = make_client_order_id(self.strategy_id, f"{self._open_signal_id or 'unknown'}:exit")
        order = Order(
            client_order_id=coid,
            symbol=symbol,
            side="sell",
            quantity=qty,
            order_type="market",
        )
        fill = self.broker.place(order, mark_price=price)
        if fill is None:
            return
        proceeds = fill.price * fill.quantity - fill.fee
        self.cash += proceeds
        self.log.append(
            DecisionEvent(
                timestamp_ms=fill.timestamp_ms,
                event_type="order_filled",
                symbol=fill.symbol,
                side="sell",
                strategy_id=self.strategy_id,
                signal_id=self._open_signal_id,
                client_order_id=coid,
                quantity=fill.quantity,
                price=fill.price,
                notional=fill.notional,
                rationale=reason,
                slippage_bps=(1.0 - fill.price / price) * 10_000.0 if price > 0 else 0.0,
            )
        )
        self._open_stop = None
        self._open_target = None
        self._open_signal_id = None
