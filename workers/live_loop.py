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

import os
import time
from collections.abc import Callable
from pathlib import Path

from data.backfill import TIMEFRAME_MS
from data.binance import OHLCVClient, fetch_ohlcv
from data.store import bars_path, load_bars, save_bars
from execution.ccxt_paper import PaperBroker
from execution.runner import Bar, Executor
from features.compute import bars_to_df
from memory.decision_log import DecisionEvent, DecisionLog
from risk.caps import DEFAULT_KILL_SWITCH_PATH, RiskCaps, kill_switch_active
from signals.types import Signal
from strategies.baseline_ema_cross import generate_signals
from tools.notifier import NoOpNotifier, Notifier, TelegramNotifier


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


# --- CLI entry --------------------------------------------------------------------------
#
# `uv run python -m workers.live_loop` reads env vars, wires the loop, runs forever.
# Override any TRADERBOT_* var in `.env` (the executor is paper-only — see I-4 for the
# go-live gates that this entry point does NOT satisfy).


_DEFAULTS: dict[str, str] = {
    "TRADERBOT_LOG_PATH": "data/decision_log/traderbot.db",
    "TRADERBOT_INITIAL_CASH": "10000",
    "TRADERBOT_SYMBOL": "BTC/USDT",
    "TRADERBOT_TIMEFRAME": "1h",
    "TRADERBOT_POLL_INTERVAL_S": "30",
    "TRADERBOT_RISK_PCT": "0.005",
    "TRADERBOT_STRATEGY_ID": "baseline_ema_cross",
    "TRADERBOT_HEARTBEAT_INTERVAL_S": "3600",
    "TRADERBOT_SLIPPAGE_BPS": "5",
    "TRADERBOT_COMMISSION_BPS": "10",
}


def _env(key: str) -> str:
    return os.environ.get(key, _DEFAULTS[key])


def build_from_env() -> tuple[LiveLoop, dict[str, str]]:
    """Wire a LiveLoop from env vars. Returns the loop and a config-summary dict."""
    log_path = Path(_env("TRADERBOT_LOG_PATH"))
    initial_cash = float(_env("TRADERBOT_INITIAL_CASH"))
    symbol = _env("TRADERBOT_SYMBOL")
    timeframe = _env("TRADERBOT_TIMEFRAME")
    poll_interval_s = float(_env("TRADERBOT_POLL_INTERVAL_S"))
    risk_pct = float(_env("TRADERBOT_RISK_PCT"))
    strategy_id = _env("TRADERBOT_STRATEGY_ID")
    heartbeat_s = float(_env("TRADERBOT_HEARTBEAT_INTERVAL_S"))
    slippage_bps = float(_env("TRADERBOT_SLIPPAGE_BPS"))
    commission_bps = float(_env("TRADERBOT_COMMISSION_BPS"))

    marks: dict[str, float] = {}
    broker = PaperBroker(
        get_price=lambda s: marks.get(s, 0.0),
        slippage_bps=slippage_bps,
        commission_bps=commission_bps,
    )
    log = DecisionLog(log_path)
    caps = RiskCaps()
    executor = Executor(
        broker=broker,
        log=log,
        strategy_id=strategy_id,
        initial_cash=initial_cash,
        caps=caps,
        risk_pct=risk_pct,
    )
    notifier = TelegramNotifier()

    loop = LiveLoop(
        symbol=symbol,
        timeframe=timeframe,
        executor=executor,
        marks=marks,
        poll_interval_s=poll_interval_s,
        notifier=notifier,
        heartbeat_interval_s=heartbeat_s,
    )

    config = {
        "symbol": symbol,
        "timeframe": timeframe,
        "log_path": str(log_path),
        "initial_cash": f"${initial_cash:,.2f}",
        "risk_pct": f"{risk_pct * 100:.2f}%",
        "poll_interval_s": str(poll_interval_s),
        "slippage_bps": str(slippage_bps),
        "commission_bps": str(commission_bps),
        "heartbeat_interval_s": str(heartbeat_s),
        "strategy_id": strategy_id,
        "telegram": "configured" if notifier.configured else "not configured (silent)",
    }
    return loop, config


def main() -> None:
    loop, cfg = build_from_env()
    print("=" * 56)
    print("   traderbot — live loop (paper mode)")
    print("=" * 56)
    for k, v in cfg.items():
        print(f"  {k:22s}  {v}")
    print()
    print("  Mode:                   PAPER (no real orders, never)")
    print("  Stop:                   Ctrl+C")
    print("  Pause without exit:     touch data/state/KILL_SWITCH")
    print("=" * 56)

    max_iters_env = os.environ.get("TRADERBOT_MAX_ITERATIONS")
    max_iters = int(max_iters_env) if max_iters_env else None

    try:
        loop.run(max_iterations=max_iters)
    except KeyboardInterrupt:
        print("\nShutdown requested. Decision log is on disk at:")
        print(f"  {cfg['log_path']}")
        print("Open the dashboard with: uv run streamlit run ui/dashboard.py")


if __name__ == "__main__":
    main()
