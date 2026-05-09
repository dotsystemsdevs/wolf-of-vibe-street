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
    """Multi-symbol-aware, one-position-per-symbol, long-only paper-mode runner."""

    def __init__(
        self,
        broker: Broker,
        log: DecisionLog,
        *,
        strategy_id: str,
        initial_cash: float,
        caps: RiskCaps | None = None,
        risk_pct: float = 0.005,
        trade_mode: str = "paper",
        on_fill=None,  # optional callback(side, symbol, price, pnl_pct=None)
        conviction_evaluator=None,  # optional ConvictionMultiplier
        conviction_context_fn=None,  # optional callable(signal, bar) → context dict
    ):
        self.broker = broker
        self.log = log
        self.strategy_id = strategy_id
        self.cash = initial_cash
        self.risk_pct = risk_pct
        self.caps = caps
        self.trade_mode = trade_mode
        # Optional fill notification — called after every successful entry/exit.
        # Used by live_loop to push real-time Telegram alerts ("Buy SOL" /
        # "SOLD SOL +2.1%"). Decoupled from the broker so tests don't pull in
        # a notifier.
        self._on_fill = on_fill
        # Optional LLM conviction multiplier (Phase 4). When set, every entry
        # asks the LLM for a [0.3, 1.5] sizing multiplier given fresh context
        # (news, regime, recent strategy P&L) and adjusts the rule-based qty.
        # Never zero — LLM cannot veto. The context_fn pulls the data the
        # evaluator needs without coupling Executor to news/strategy modules.
        self._conviction_evaluator = conviction_evaluator
        self._conviction_context_fn = conviction_context_fn
        self.daily_high_water = initial_cash
        self.weekly_high_water = initial_cash
        self._current_day: int | None = None
        self._current_week: tuple[int, int] | None = None
        self._open_stops: dict[str, float] = {}
        self._open_targets: dict[str, float] = {}
        self._open_signal_ids: dict[str, str] = {}
        # Track entry prices for accurate realized-% on exit.
        self._open_entry_prices: dict[str, float] = {}

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

        sym = signal.symbol
        sym_stop = self._open_stops.get(sym)
        sym_target = self._open_targets.get(sym)
        # Intra-bar stop/target check. Direction-aware: for longs the stop is
        # BELOW entry (bar.low triggers it) and target ABOVE (bar.high triggers).
        # For shorts the directions invert: stop ABOVE entry, target BELOW.
        if open_pos is not None and sym_stop is not None and sym_target is not None:
            exit_reason: str | None = None
            exit_price: float | None = None
            is_short = open_pos.quantity < 0
            if is_short:
                if bar.high >= sym_stop:
                    exit_reason, exit_price = "stop_hit", sym_stop
                elif bar.low <= sym_target:
                    exit_reason, exit_price = "target_hit", sym_target
            else:
                if bar.low <= sym_stop:
                    exit_reason, exit_price = "stop_hit", sym_stop
                elif bar.high >= sym_target:
                    exit_reason, exit_price = "target_hit", sym_target
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
            self._try_enter(signal, bar, direction="long")
        elif signal.side == "short" and open_pos is None and signal.stop is not None:
            self._try_enter(signal, bar, direction="short")
        elif signal.side == "sell" and open_pos is not None and open_pos.quantity > 0:
            self._exit(open_pos.symbol, open_pos.quantity, bar.close, "signal_exit", bar)
        elif signal.side == "cover" and open_pos is not None and open_pos.quantity < 0:
            self._exit(open_pos.symbol, open_pos.quantity, bar.close, "signal_exit", bar)

    def _try_enter(self, signal: Signal, bar: Bar, *, direction: str = "long") -> None:
        """Open a long (direction='long') or short (direction='short') position.

        Long: broker side='buy', position qty positive, cash decreases by notional+fee.
        Short: broker side='sell' (sell-to-open), position qty negative, cash
               increases by proceeds-fee (we receive the borrowed-coin sale).
        """
        assert signal.stop is not None
        eq = self.equity({signal.symbol: bar.close})
        raw_qty = position_size(eq, bar.close, signal.stop, risk_pct=self.risk_pct)
        if raw_qty <= 0:
            return

        # Phase 4: optionally adjust qty by LLM conviction multiplier. Always
        # bounded to [0.3 * raw, 1.5 * raw] — LLM cannot zero-out a trade.
        # On any error/timeout the wrapper returns multiplier=1.0 so we
        # degrade gracefully to rule-based sizing.
        conviction = None
        if self._conviction_evaluator is not None:
            ctx = (
                self._conviction_context_fn(signal, bar)
                if self._conviction_context_fn
                else {}
            )
            conviction = self._conviction_evaluator.evaluate(
                signal,
                entry_price=bar.close,
                **ctx,
            )
            qty = raw_qty * conviction.multiplier
        else:
            qty = raw_qty
        notional = qty * bar.close
        broker_side = "buy" if direction == "long" else "sell"

        if self.caps is not None:
            state = RiskState(
                equity_now=eq,
                daily_high_water=self.daily_high_water,
                weekly_high_water=self.weekly_high_water,
                open_positions_count=len(self.broker.positions()),
                open_total_notional_usd=sum(abs(p.notional) for p in self.broker.positions()),
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

        coid = make_client_order_id(self.strategy_id, signal.symbol, str(signal.timestamp_ms))
        order = Order(
            client_order_id=coid,
            symbol=signal.symbol,
            side=broker_side,
            quantity=qty,
            order_type="market",
        )
        self.log.append(
            DecisionEvent(
                timestamp_ms=signal.timestamp_ms,
                event_type="order_placed",
                symbol=signal.symbol,
                side=signal.side,  # "buy" or "short" — preserves intent in log
                strategy_id=self.strategy_id,
                signal_id=str(signal.timestamp_ms),
                client_order_id=coid,
                quantity=qty,
                price=bar.close,
                notional=notional,
                metadata={
                    "stop": signal.stop,
                    "target": signal.target,
                    "direction": direction,
                    # A/B logging for Phase 4: persist both raw + adjusted qty
                    # and the multiplier so analyzer can compute counterfactual
                    # P&L for the no-LLM case.
                    "raw_qty": raw_qty,
                    "conviction_mult": conviction.multiplier if conviction else None,
                    "conviction_reasoning": conviction.reasoning if conviction else None,
                    "conviction_cost_usd": conviction.cost_usd if conviction else None,
                    "conviction_fallback": conviction.fallback if conviction else None,
                },
            )
        )
        fill = self.broker.place(order, mark_price=bar.close, timestamp_ms=bar.timestamp_ms)
        if fill is None:
            self.log.append(
                DecisionEvent(
                    timestamp_ms=signal.timestamp_ms,
                    event_type="order_rejected",
                    symbol=signal.symbol,
                    side=signal.side,
                    strategy_id=self.strategy_id,
                    signal_id=str(signal.timestamp_ms),
                    client_order_id=coid,
                    rationale="broker returned None",
                )
            )
            return
        # Cash flow flips by direction: long pays out for the buy, short receives
        # proceeds (mark-to-market via short-sale of borrowed coin in real perp market).
        if direction == "long":
            self.cash -= fill.price * fill.quantity + fill.fee
        else:
            self.cash += fill.price * fill.quantity - fill.fee
        self._open_stops[signal.symbol] = signal.stop
        self._open_targets[signal.symbol] = signal.target
        self._open_signal_ids[signal.symbol] = str(signal.timestamp_ms)
        self._open_entry_prices[signal.symbol] = float(fill.price)
        # Real-time entry alert (Telegram in live).
        if self._on_fill is not None:
            try:
                self._on_fill(direction, signal.symbol, float(fill.price), None)
            except Exception:  # noqa: BLE001
                pass
        self.log.append(
            DecisionEvent(
                timestamp_ms=fill.timestamp_ms,
                event_type="order_filled",
                symbol=fill.symbol,
                side=signal.side,  # "buy" for long entry, "short" for short entry
                strategy_id=self.strategy_id,
                signal_id=self._open_signal_ids.get(signal.symbol),
                client_order_id=coid,
                quantity=fill.quantity,
                price=fill.price,
                notional=fill.notional,
                slippage_bps=(fill.price / bar.close - 1.0) * 10_000.0,
                metadata={"fee": fill.fee, "mode": self.trade_mode, "direction": direction},
            )
        )

    def _exit(self, symbol: str, qty: float, price: float, reason: str, bar: Bar) -> None:
        """Close a position. `qty` is signed: positive = long position to sell,
        negative = short position to cover. Cash flow flips accordingly."""
        if qty == 0:
            return
        is_short = qty < 0
        abs_qty = abs(qty)
        signal_id = self._open_signal_ids.get(symbol) or "unknown"
        coid = make_client_order_id(self.strategy_id, symbol, f"{signal_id}:exit")
        # Closing a short = buy-to-cover; closing a long = sell-to-close.
        broker_side = "buy" if is_short else "sell"
        order = Order(
            client_order_id=coid,
            symbol=symbol,
            side=broker_side,
            quantity=abs_qty,
            order_type="market",
        )
        fill = self.broker.place(order, mark_price=price, timestamp_ms=bar.timestamp_ms)
        if fill is None:
            return
        # Long close → cash += sale proceeds. Short cover → cash -= buyback cost.
        if is_short:
            self.cash -= fill.price * fill.quantity + fill.fee
        else:
            self.cash += fill.price * fill.quantity - fill.fee
        self.log.append(
            DecisionEvent(
                timestamp_ms=fill.timestamp_ms,
                event_type="order_filled",
                symbol=fill.symbol,
                side="cover" if is_short else "sell",
                strategy_id=self.strategy_id,
                signal_id=signal_id,
                client_order_id=coid,
                quantity=fill.quantity,
                price=fill.price,
                notional=fill.notional,
                rationale=reason,
                slippage_bps=(1.0 - fill.price / price) * 10_000.0 if price > 0 else 0.0,
                metadata={"fee": fill.fee, "mode": self.trade_mode},
            )
        )
        # Compute realized % return for the fill alert (long: exit/entry-1; short
        # inverts because price-down is profit). Falls back to 0% if entry price
        # was never recorded (legacy log rows pre-2026-05-03).
        entry_px = self._open_entry_prices.pop(symbol, 0.0)
        pnl_pct: float | None = None
        if entry_px > 0:
            if is_short:
                pnl_pct = (entry_px / float(fill.price) - 1.0) * 100.0
            else:
                pnl_pct = (float(fill.price) / entry_px - 1.0) * 100.0
        self._open_stops.pop(symbol, None)
        self._open_targets.pop(symbol, None)
        self._open_signal_ids.pop(symbol, None)
        # Real-time exit alert (Telegram in live).
        if self._on_fill is not None:
            try:
                exit_side = "cover" if is_short else "sell"
                self._on_fill(exit_side, symbol, float(fill.price), pnl_pct)
            except Exception:  # noqa: BLE001
                pass
