"""Tests for daily/weekly high-water reset in Executor.on_bar.

Real bug from session 12 live verify: HW only ratcheted up, never reset at UTC dawn /
ISO-week rollover, so a small early dip locked the executor out of all subsequent
entries via daily_drawdown_halt. These tests pin the rollover behavior.
"""

from __future__ import annotations

from pathlib import Path

from execution.ccxt_paper import PaperBroker
from execution.runner import Bar, Executor
from memory.decision_log import DecisionLog
from signals.types import Signal

DAY_MS = 86_400_000
HOUR_MS = 3_600_000

#  2024-04-22 00:00 UTC = 1713744000000  (a Monday)
MONDAY_00_UTC = 1_713_744_000_000


def _ex(tmp_path: Path) -> Executor:
    broker = PaperBroker(get_price=lambda _: 100.0, slippage_bps=0.0, commission_bps=0.0)
    log = DecisionLog(tmp_path / "log.db")
    return Executor(broker=broker, log=log, strategy_id="t", initial_cash=10_000.0, risk_pct=0.005)


def _hold(ts: int) -> Signal:
    return Signal(ts, "BTC/USDT", "hold", 0.0, None, None, "")


def _bar(ts: int, c: float = 100.0) -> Bar:
    return Bar(timestamp_ms=ts, high=c + 1, low=c - 1, close=c)


def test_daily_hw_resets_on_utc_dawn(tmp_path: Path) -> None:
    """Day 1: equity peaks at 10,500 → daily HW = 10,500.
    Day 2 first bar: HW resets to current equity (which is back at 10,000)."""
    ex = _ex(tmp_path)
    ex.cash = 10_500.0
    ex.on_bar(_hold(MONDAY_00_UTC + 12 * HOUR_MS), _bar(MONDAY_00_UTC + 12 * HOUR_MS))
    assert ex.daily_high_water == 10_500.0

    ex.cash = 10_000.0
    ex.on_bar(_hold(MONDAY_00_UTC + DAY_MS), _bar(MONDAY_00_UTC + DAY_MS))
    assert ex.daily_high_water == 10_000.0


def test_daily_hw_does_not_reset_within_same_day(tmp_path: Path) -> None:
    """Two bars in same UTC day → HW preserved (and ratcheted)."""
    ex = _ex(tmp_path)
    ex.cash = 10_500.0
    ex.on_bar(_hold(MONDAY_00_UTC + HOUR_MS), _bar(MONDAY_00_UTC + HOUR_MS))
    assert ex.daily_high_water == 10_500.0

    ex.cash = 10_300.0
    ex.on_bar(_hold(MONDAY_00_UTC + 2 * HOUR_MS), _bar(MONDAY_00_UTC + 2 * HOUR_MS))
    assert ex.daily_high_water == 10_500.0  # ratcheted, not reset


def test_weekly_hw_resets_at_iso_week_rollover(tmp_path: Path) -> None:
    """Sunday 23:00 UTC and the following Monday 00:00 UTC are different ISO weeks."""
    ex = _ex(tmp_path)
    sunday_23 = MONDAY_00_UTC - HOUR_MS
    next_monday_00 = MONDAY_00_UTC + 7 * DAY_MS

    ex.cash = 10_800.0
    ex.on_bar(_hold(sunday_23), _bar(sunday_23))
    week_a_hw = ex.weekly_high_water
    assert week_a_hw == 10_800.0

    ex.cash = 10_200.0
    ex.on_bar(_hold(next_monday_00), _bar(next_monday_00))
    assert ex.weekly_high_water == 10_200.0
