"""Tests for execution.runner.Executor — bar-driven signal → caps → broker → log."""

from __future__ import annotations

from pathlib import Path

from execution.ccxt_paper import PaperBroker
from execution.runner import Bar, Executor
from memory.decision_log import DecisionLog
from risk.caps import RiskCaps
from signals.types import Signal


def _executor(tmp_path: Path, *, price: float = 100.0, caps: RiskCaps | None = None) -> Executor:
    broker = PaperBroker(
        get_price=lambda _: price,
        slippage_bps=0.0,
        commission_bps=0.0,
        clock_ms=lambda: 0,
    )
    log = DecisionLog(tmp_path / "log.db")
    return Executor(
        broker=broker,
        log=log,
        strategy_id="test",
        initial_cash=10_000.0,
        caps=caps,
    )


def _hold(ts: int = 0) -> Signal:
    return Signal(ts, "BTC/USDT", "hold", 0.0, None, None, "")


def _buy(ts: int = 0, *, stop: float = 95.0, target: float = 110.0) -> Signal:
    return Signal(ts, "BTC/USDT", "buy", 0.5, stop, target, "test")


def _sell(ts: int = 0) -> Signal:
    return Signal(ts, "BTC/USDT", "sell", 0.0, None, None, "test_exit")


def _bar(ts: int = 0, high: float = 101.0, low: float = 99.0, close: float = 100.0) -> Bar:
    return Bar(timestamp_ms=ts, high=high, low=low, close=close)


def test_expected_buy_signal_opens_position_and_logs(tmp_path: Path) -> None:
    """Happy: buy signal with valid stop → position opens, log gets signal+order_placed+order_filled."""
    ex = _executor(tmp_path, price=100.0)
    ex.on_bar(_buy(), _bar())

    positions = ex.broker.positions()
    assert len(positions) == 1
    assert positions[0].quantity > 0

    rows = ex.log.all()
    types = [r["event_type"] for r in rows]
    assert "signal" in types
    assert "order_placed" in types
    assert "order_filled" in types


def test_intra_bar_stop_hit_exits_at_stop(tmp_path: Path) -> None:
    """Edge: open position, next bar has low <= stop → exit at stop, position closes."""
    ex = _executor(tmp_path, price=100.0)
    ex.on_bar(_buy(stop=95.0, target=120.0), _bar())
    assert len(ex.broker.positions()) == 1

    ex.on_bar(_hold(), _bar(high=101.0, low=94.0, close=98.0))
    assert ex.broker.positions() == []
    rows = ex.log.all()
    exit_rows = [r for r in rows if r["event_type"] == "order_filled" and r["side"] == "sell"]
    assert len(exit_rows) == 1
    assert exit_rows[0]["rationale"] == "stop_hit"


def test_intra_bar_target_hit_exits_at_target(tmp_path: Path) -> None:
    ex = _executor(tmp_path, price=100.0)
    ex.on_bar(_buy(stop=90.0, target=105.0), _bar())

    ex.on_bar(_hold(), _bar(high=110.0, low=99.0, close=108.0))
    assert ex.broker.positions() == []
    rows = ex.log.all()
    exit_rows = [r for r in rows if r["event_type"] == "order_filled" and r["side"] == "sell"]
    assert exit_rows[0]["rationale"] == "target_hit"
    assert exit_rows[0]["price"] == 105.0


def test_kill_switch_blocks_entry_and_logs_risk_block(tmp_path: Path, monkeypatch) -> None:
    """Failure: kill switch → buy signal logs `risk_block`, no order placed."""
    monkeypatch.setenv("KILL_SWITCH", "true")
    caps = RiskCaps(kill_switch_path=tmp_path / "missing")
    ex = _executor(tmp_path, caps=caps)

    ex.on_bar(_buy(), _bar())
    assert ex.broker.positions() == []

    rows = ex.log.all()
    block_rows = [r for r in rows if r["event_type"] == "risk_block"]
    assert len(block_rows) == 1
    assert block_rows[0]["rationale"] == "kill_switch"


def test_signal_exit_closes_position(tmp_path: Path) -> None:
    ex = _executor(tmp_path, price=100.0)
    ex.on_bar(_buy(stop=90.0, target=200.0), _bar())
    ex.on_bar(_sell(), _bar(close=100.0))
    assert ex.broker.positions() == []
    rows = ex.log.all()
    exits = [r for r in rows if r["event_type"] == "order_filled" and r["side"] == "sell"]
    assert exits[0]["rationale"] == "signal_exit"


def test_no_buy_when_already_long(tmp_path: Path) -> None:
    """Edge: a second buy while still long is a no-op (single-position invariant)."""
    ex = _executor(tmp_path, price=100.0)
    ex.on_bar(_buy(), _bar())
    qty_before = ex.broker.positions()[0].quantity
    ex.on_bar(_buy(ts=1), _bar(ts=1))
    qty_after = ex.broker.positions()[0].quantity
    assert qty_after == qty_before
