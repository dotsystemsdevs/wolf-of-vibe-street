"""Tests for workers.live_loop — bar-detection + persistence + executor wiring."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from execution.ccxt_paper import PaperBroker
from execution.runner import Executor
from memory.decision_log import DecisionLog
from signals.types import Signal
from workers.live_loop import LiveLoop

HOUR_MS = 3_600_000


def _row(ts: int, c: float = 100.0) -> list[float]:
    return [ts, c, c + 1, c - 1, c + 0.5, 10.0]


class _FakeClient:
    def __init__(self) -> None:
        self.pages: list[list[list[float]]] = []
        self.calls = 0

    def push(self, rows: list[list[float]]) -> None:
        self.pages.append(rows)

    def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=None):  # noqa: ARG002
        self.calls += 1
        return self.pages.pop(0) if self.pages else []


def _make_loop(
    tmp_path: Path, *, now_ms: list[int]
) -> tuple[LiveLoop, _FakeClient, dict, Executor]:
    """Build a wired LiveLoop with fake exchange + clock + isolated parquet root."""
    marks: dict[str, float] = {}
    broker = PaperBroker(
        get_price=lambda s: marks.get(s, 0.0), slippage_bps=0.0, commission_bps=0.0
    )
    log = DecisionLog(tmp_path / "log.db")
    ex = Executor(broker=broker, log=log, strategy_id="test", initial_cash=10_000.0, risk_pct=0.005)

    client = _FakeClient()

    def all_holds(df: pd.DataFrame, **kw):
        return [
            Signal(
                timestamp_ms=int(t),
                symbol=kw.get("symbol", "BTC/USDT"),
                side="hold",
                conviction=0.0,
                stop=None,
                target=None,
                rationale="",
            )
            for t in df["timestamp_ms"]
        ]

    loop = LiveLoop(
        "BTC/USDT",
        "1h",
        executor=ex,
        marks=marks,
        client=client,
        clock_ms=lambda: now_ms[0],
        poll_interval_s=0.0,
        strategy_fn=all_holds,
    )
    loop.parquet_path = tmp_path / "bars.parquet"
    return loop, client, marks, ex


def test_expected_first_tick_processes_closed_bar(tmp_path: Path) -> None:
    """A bar whose close-time is <= now should be picked up. The current in-progress bar is skipped."""
    now = [3 * HOUR_MS + 100]  # we're 100ms into hour 3 → bar at 2*HOUR_MS just closed
    loop, client, marks, ex = _make_loop(tmp_path, now_ms=now)

    client.push([_row(0), _row(HOUR_MS), _row(2 * HOUR_MS), _row(3 * HOUR_MS)])

    n = loop.tick()
    assert n == 3  # bars at 0, HOUR_MS, 2*HOUR_MS — the 3*HOUR_MS bar is still in-progress
    assert marks["BTC/USDT"] == _row(2 * HOUR_MS)[4]  # close of the latest closed bar


def test_idempotency_no_new_bars_returns_zero(tmp_path: Path) -> None:
    """Second tick with no new closed bars → no work done."""
    now = [3 * HOUR_MS + 100]
    loop, client, marks, ex = _make_loop(tmp_path, now_ms=now)

    client.push([_row(0), _row(HOUR_MS), _row(2 * HOUR_MS), _row(3 * HOUR_MS)])
    loop.tick()

    client.push([_row(0), _row(HOUR_MS), _row(2 * HOUR_MS), _row(3 * HOUR_MS)])
    n = loop.tick()
    assert n == 0


def test_advances_when_new_bar_closes(tmp_path: Path) -> None:
    """After tick #1, hour-3 bar still in-progress. Move clock forward and tick again."""
    now = [3 * HOUR_MS + 100]
    loop, client, marks, ex = _make_loop(tmp_path, now_ms=now)

    client.push([_row(0), _row(HOUR_MS), _row(2 * HOUR_MS), _row(3 * HOUR_MS)])
    loop.tick()
    assert loop._last_processed_ts[loop.symbols[0]] == 2 * HOUR_MS

    now[0] = 4 * HOUR_MS + 100
    client.push([_row(0), _row(HOUR_MS), _row(2 * HOUR_MS), _row(3 * HOUR_MS), _row(4 * HOUR_MS)])
    n = loop.tick()
    assert n == 1
    assert loop._last_processed_ts[loop.symbols[0]] == 3 * HOUR_MS


def test_checkpoint_loaded_from_log_skips_replay(tmp_path: Path) -> None:
    """A fresh loop with prior signals in the log should NOT replay those bars.

    Real bug: every restart replayed the last 10 fetched bars and re-fired
    BUY/SELL fills. This test creates a log with a signal at HOUR_MS, then
    builds a new loop and verifies the checkpoint was seeded from the log.
    """
    now = [3 * HOUR_MS + 100]
    loop, client, _, ex = _make_loop(tmp_path, now_ms=now)
    # Simulate a prior session: log a signal for the symbol at HOUR_MS.
    from memory.decision_log import DecisionEvent

    ex.log.append(
        DecisionEvent(
            timestamp_ms=HOUR_MS,
            event_type="signal",
            symbol="BTC/USDT",
            side="hold",
            strategy_id="test",
            signal_id=str(HOUR_MS),
            rationale="prior session",
        )
    )
    # New LiveLoop sharing the same executor/log → checkpoint seeded.
    from workers.live_loop import LiveLoop

    fresh = LiveLoop(
        "BTC/USDT",
        "1h",
        executor=ex,
        marks={},
        client=client,
        clock_ms=lambda: now[0],
        poll_interval_s=0.0,
        strategy_fn=loop.strategy_fn,
    )
    assert fresh._last_processed_ts["BTC/USDT"] == HOUR_MS

    # Now push four bars including the already-processed one. Only bars > HOUR_MS
    # AND closed should fire — that's bar at 2*HOUR_MS only (bar at 3*HOUR_MS is
    # still in progress at now=3*HOUR_MS+100).
    fresh.parquet_path = tmp_path / "bars2.parquet"
    client.push([_row(0), _row(HOUR_MS), _row(2 * HOUR_MS), _row(3 * HOUR_MS)])
    n = fresh.tick()
    assert n == 1


def test_kill_switch_pauses_run(tmp_path: Path, monkeypatch) -> None:
    """run() with kill switch on logs the pause and does NOT call tick()."""
    monkeypatch.setenv("KILL_SWITCH", "true")
    now = [HOUR_MS]
    loop, client, marks, ex = _make_loop(tmp_path, now_ms=now)
    client.push([_row(0), _row(HOUR_MS)])

    loop.run(max_iterations=2)

    rows = ex.log.all()
    blocks = [
        r
        for r in rows
        if r["event_type"] == "risk_block" and "kill_switch" in (r["rationale"] or "")
    ]
    assert len(blocks) == 2
    assert client.calls == 0  # never polled
