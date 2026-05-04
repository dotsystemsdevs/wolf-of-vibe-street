"""Tests for LiveLoop notifier integration: kill switch transitions, errors, heartbeat."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from execution.ccxt_paper import PaperBroker
from execution.runner import Executor
from memory.decision_log import DecisionLog
from signals.types import Signal
from tools.notifier import Notifier
from workers.live_loop import LiveLoop


class _RecordingNotifier(Notifier):
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []

    def notify(self, level: str, title: str, body: str = "") -> None:
        self.events.append((level, title, body))


class _FakeClient:
    def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=None) -> list[list[float]]:  # noqa: ARG002
        return []


def _all_holds(df: pd.DataFrame, **kw: Any) -> list[Signal]:
    return [
        Signal(int(t), kw.get("symbol", "BTC/USDT"), "hold", 0.0, None, None, "")
        for t in df["timestamp_ms"]
    ]


def _loop(tmp_path: Path, *, now: list[int]) -> tuple[LiveLoop, _RecordingNotifier, Executor]:
    marks: dict[str, float] = {}
    broker = PaperBroker(get_price=lambda s: marks.get(s, 0.0), slippage_bps=0, commission_bps=0)
    log = DecisionLog(tmp_path / "log.db")
    ex = Executor(broker=broker, log=log, strategy_id="t", initial_cash=10_000.0, risk_pct=0.005)
    notifier = _RecordingNotifier()
    loop = LiveLoop(
        "BTC/USDT",
        "1h",
        executor=ex,
        marks=marks,
        client=_FakeClient(),
        clock_ms=lambda: now[0],
        poll_interval_s=0.0,
        strategy_fn=_all_holds,
        notifier=notifier,
        heartbeat_interval_s=10.0,
    )
    loop.parquet_path = tmp_path / "bars.parquet"
    return loop, notifier, ex


def test_kill_switch_alerts_only_on_state_change(tmp_path: Path, monkeypatch) -> None:
    """Kill switch ON: notify once. Stays ON: no spam. Goes OFF: notify once."""
    now = [0]
    loop, notifier, _ = _loop(tmp_path, now=now)

    monkeypatch.setenv("KILL_SWITCH", "true")
    loop.run(max_iterations=3)

    titles = [t for _, t, _ in notifier.events]
    assert titles.count("Kill switch ON") == 1
    assert "Kill switch OFF" not in titles

    monkeypatch.delenv("KILL_SWITCH", raising=False)
    loop.run(max_iterations=2)

    titles = [t for _, t, _ in notifier.events]
    assert titles.count("Kill switch OFF") == 1


def test_tick_error_notifies(tmp_path: Path) -> None:
    now = [0]
    loop, notifier, _ = _loop(tmp_path, now=now)

    def _exploding(*args, **kwargs):
        raise RuntimeError("binance 502")

    loop.client = type("C", (), {"fetch_ohlcv": staticmethod(_exploding)})()
    loop.run(max_iterations=1)

    errs = [(level, title, body) for level, title, body in notifier.events if level == "ERROR"]
    assert len(errs) == 1
    assert "binance 502" in errs[0][2]


def test_heartbeat_does_not_fire_telegram(tmp_path: Path) -> None:
    """Heartbeat Telegram pings were removed 2026-05-04 per operator request —
    they were noisy noise without operational signal. The 6×/day daily-summary
    cron is the alive-check now. Loop must NOT emit any 'heartbeat'-titled
    notifications regardless of how long it runs."""
    now = [0]
    loop, notifier, _ = _loop(tmp_path, now=now)

    loop.run(max_iterations=1)
    now[0] = 5_000
    loop.run(max_iterations=1)
    now[0] = 30_000
    loop.run(max_iterations=1)
    hbs = [t for _level, t, _body in notifier.events if t == "heartbeat"]
    assert hbs == []
