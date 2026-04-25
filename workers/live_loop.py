"""Polling-driven live loop — bridges historical-replay and continuous live trading.

Polls Binance REST for the latest bar every `poll_interval_s`. When a NEW closed bar
appears, persists it to Parquet, recomputes the strategy on the full history, and
hands the new bar (with its signal) to the executor. The single position model + same
risk caps + same decision log apply.

Why polling instead of WebSocket: Binance hour-bar finality is `closeTime <= now`. A
30-second poll catches it within 30s of close — well under any meaningful slippage on
1h bars. WebSocket adds async machinery for no Phase-1 benefit.

`tick()` is one iteration; `run()` is the forever loop. Tests drive `tick()` directly
with a fake client + fake clock.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from data.backfill import TIMEFRAME_MS
from data.binance import OHLCVClient, fetch_ohlcv
from data.store import bars_path, load_bars, save_bars
from execution.runner import Bar, Executor
from features.compute import bars_to_df
from memory.decision_log import DecisionEvent
from risk.caps import DEFAULT_KILL_SWITCH_PATH, kill_switch_active
from signals.types import Signal
from strategies.baseline_ema_cross import generate_signals
from tools.notifier import NoOpNotifier, Notifier


class LiveLoop:
    def __init__(
        self,
        symbol: str,
        timeframe: str,
        executor: Executor,
        marks: dict[str, float],
        *,
        exchange: str = "binance",
        client: OHLCVClient | None = None,
        clock_ms: Callable[[], int] | None = None,
        poll_interval_s: float = 30.0,
        strategy_fn: Callable[..., list[Signal]] | None = None,
        kill_switch_path: Path = DEFAULT_KILL_SWITCH_PATH,
        notifier: Notifier | None = None,
        heartbeat_interval_s: float = 3600.0,
    ):
        if timeframe not in TIMEFRAME_MS:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}")
        self.symbol = symbol
        self.timeframe = timeframe
        self.executor = executor
        self.marks = marks
        self.exchange = exchange
        self.client = client
        self.clock = clock_ms or (lambda: int(time.time() * 1000))
        self.poll_interval_s = poll_interval_s
        self.strategy_fn = strategy_fn or generate_signals
        self.kill_switch_path = kill_switch_path
        self.notifier: Notifier = notifier or NoOpNotifier()
        self.heartbeat_interval_s = heartbeat_interval_s
        self.parquet_path = bars_path(exchange, symbol, timeframe)
        self._last_processed_ts: int | None = None
        self._tf_ms = TIMEFRAME_MS[timeframe]
        self._kill_alerted = False
        self._last_heartbeat_ms: int | None = None

    def tick(self) -> int:
        """Process all newly-closed bars since last call. Returns count processed."""
        now_ms = self.clock()
        recent = fetch_ohlcv(self.symbol, timeframe=self.timeframe, limit=10, client=self.client)
        if not recent:
            return 0

        last = self._last_processed_ts
        new_bars = [
            b
            for b in recent
            if (last is None or b["timestamp_ms"] > last)
            and b["timestamp_ms"] + self._tf_ms <= now_ms
        ]
        if not new_bars:
            return 0

        save_bars(new_bars, self.parquet_path)

        all_bars = load_bars(self.parquet_path)
        df = bars_to_df(all_bars)
        if df.empty:
            return 0
        signals = self.strategy_fn(df, symbol=self.symbol)
        ts_to_idx = {int(t): i for i, t in enumerate(df["timestamp_ms"].tolist())}

        for new in new_bars:
            idx = ts_to_idx.get(new["timestamp_ms"])
            if idx is None:
                continue
            self.marks[self.symbol] = float(new["close"])
            bar = Bar(
                timestamp_ms=new["timestamp_ms"],
                high=float(new["high"]),
                low=float(new["low"]),
                close=float(new["close"]),
            )
            self.executor.on_bar(signals[idx], bar)
            self._last_processed_ts = new["timestamp_ms"]

        return len(new_bars)

    def run(self, *, max_iterations: int | None = None) -> None:
        """Forever loop. Honors kill switch (pauses, doesn't exit). Logs + notifies."""
        i = 0
        while max_iterations is None or i < max_iterations:
            now_ms = self.clock()

            if kill_switch_active(self.kill_switch_path):
                self.executor.log.append(
                    DecisionEvent(
                        timestamp_ms=now_ms,
                        event_type="risk_block",
                        symbol=self.symbol,
                        strategy_id=self.executor.strategy_id,
                        rationale="kill_switch_paused_loop",
                    )
                )
                if not self._kill_alerted:
                    self.notifier.notify(
                        "WARN", "Kill switch ON", f"Bot paused. Symbol: {self.symbol}."
                    )
                    self._kill_alerted = True
            else:
                if self._kill_alerted:
                    self.notifier.notify("INFO", "Kill switch OFF", "Bot resumed.")
                    self._kill_alerted = False
                try:
                    self.tick()
                except Exception as e:
                    self.executor.log.append(
                        DecisionEvent(
                            timestamp_ms=now_ms,
                            event_type="order_rejected",
                            symbol=self.symbol,
                            strategy_id=self.executor.strategy_id,
                            rationale=f"tick_error: {type(e).__name__}: {e}",
                        )
                    )
                    self.notifier.notify("ERROR", "Tick failed", f"{type(e).__name__}: {e}")

            if (
                self._last_heartbeat_ms is None
                or now_ms - self._last_heartbeat_ms >= self.heartbeat_interval_s * 1000
            ):
                self.notifier.notify(
                    "INFO",
                    "heartbeat",
                    (
                        f"symbol={self.symbol} tick={i} "
                        f"last_processed_ts={self._last_processed_ts} "
                        f"cash=${self.executor.cash:,.2f}"
                    ),
                )
                self._last_heartbeat_ms = now_ms

            time.sleep(self.poll_interval_s)
            i += 1
