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
        reconcile_interval_s: float = 4 * 3600.0,
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
        # Mid-session reconcile cadence. Default 4h: catches drift between
        # the loop's local picture and the broker's, without hammering the
        # rate-limited fetch_balance/fetch_open_orders endpoints. Live mode
        # raises on mismatch (notifier alert, but loop continues — operator
        # still has to manually intervene).
        self.reconcile_interval_s = reconcile_interval_s
        self.parquet_path = bars_path(exchange, symbol, timeframe)
        self._last_processed_ts: int | None = None
        self._tf_ms = TIMEFRAME_MS[timeframe]
        self._kill_alerted = False
        self._last_heartbeat_ms: int | None = None
        self._last_reconcile_ms: int | None = None

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

            # Periodic mid-session reconcile — every reconcile_interval_s, pull
            # broker positions/open orders, compare against decision-log-derived
            # state. Logs a `reconcile` row + Telegram alert on mismatch. Does
            # NOT halt the loop — operator decides whether to intervene.
            if (
                self._last_reconcile_ms is None
                or now_ms - self._last_reconcile_ms >= self.reconcile_interval_s * 1000
            ):
                try:
                    from execution.reconcile import reconcile  # noqa: PLC0415

                    rec = reconcile(self.executor.broker, self.executor.log.all())
                    self.executor.log.append(
                        DecisionEvent(
                            timestamp_ms=now_ms,
                            event_type="reconcile",
                            symbol=self.symbol,
                            strategy_id=self.executor.strategy_id,
                            rationale=f"periodic: {rec.summary()}",
                            metadata={
                                "is_clean": rec.is_clean,
                                "broker_positions": len(rec.broker_positions),
                                "log_positions": len(rec.log_positions),
                                "open_orders": rec.open_orders_count,
                                "mismatches": [
                                    {
                                        "symbol": m.symbol,
                                        "broker_qty": m.broker_qty,
                                        "log_qty": m.log_qty,
                                    }
                                    for m in rec.mismatches
                                ],
                                "kind": "periodic",
                            },
                        )
                    )
                    if not rec.is_clean:
                        self.notifier.notify(
                            "ERROR",
                            "Reconcile mismatch (periodic)",
                            rec.summary(),
                        )
                except Exception as e:  # noqa: BLE001
                    # Reconcile failures must not crash the loop. Log + notify.
                    self.notifier.notify(
                        "ERROR",
                        "Periodic reconcile failed",
                        f"{type(e).__name__}: {e}",
                    )
                self._last_reconcile_ms = now_ms

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
    """Wire a LiveLoop from env vars. Returns the loop and a config-summary dict.

    `TRADERBOT_STRATEGY` (snake_case id, e.g. "mean_reversion_rsi") selects the
    signal generator. Falls back to `TRADERBOT_STRATEGY_ID` for back-compat with
    older .env files. Unknown ids raise ValueError so a typo can't silently
    fall back to baseline.
    """
    from backtest.compare import (  # noqa: PLC0415  — defer import; large dep tree
        DEFAULT_STRATEGY_ID,
        strategy_by_id,
    )

    log_path = Path(_env("TRADERBOT_LOG_PATH"))
    initial_cash = float(_env("TRADERBOT_INITIAL_CASH"))
    symbol = _env("TRADERBOT_SYMBOL")
    timeframe = _env("TRADERBOT_TIMEFRAME")
    poll_interval_s = float(_env("TRADERBOT_POLL_INTERVAL_S"))
    risk_pct = float(_env("TRADERBOT_RISK_PCT"))
    # Prefer TRADERBOT_STRATEGY; fall back to legacy TRADERBOT_STRATEGY_ID.
    strategy_id = (
        os.environ.get("TRADERBOT_STRATEGY")
        or os.environ.get("TRADERBOT_STRATEGY_ID")
        or DEFAULT_STRATEGY_ID
    )
    strategy_entry = strategy_by_id(strategy_id)  # raises if unknown
    heartbeat_s = float(_env("TRADERBOT_HEARTBEAT_INTERVAL_S"))
    slippage_bps = float(_env("TRADERBOT_SLIPPAGE_BPS"))
    commission_bps = float(_env("TRADERBOT_COMMISSION_BPS"))

    marks: dict[str, float] = {}
    # Broker selection — paper by default; KrakenBroker only when LIVE_TRADING=true
    # AND TRADERBOT_BROKER=kraken. The is_live_trading_enabled() check is
    # belt-and-suspenders — KrakenBroker's own constructor also asserts it.
    from risk.live_gate import (  # noqa: PLC0415
        LIVE_CALIBRATION_MODE,
        PAPER_MODE,
        is_live_trading_enabled,
    )

    broker_name = os.environ.get("TRADERBOT_BROKER", "paper").strip().lower()
    if broker_name == "kraken":
        if not is_live_trading_enabled():
            raise RuntimeError(
                "TRADERBOT_BROKER=kraken requires LIVE_TRADING=true in the "
                "environment. The bot refuses to construct a real-money broker "
                "without the explicit opt-in flag."
            )
        from execution.ccxt_kraken import KrakenBroker  # noqa: PLC0415  — defer import

        kraken_dry_run = os.environ.get("KRAKEN_DRY_RUN", "true").strip().lower() == "true"

        # Real-money path needs BOTH the env flag (already checked) AND a fresh
        # human-gate session token. Dry-run is exempt — useful for testing the
        # Kraken adapter wiring without needing to type LIVE every 24h.
        if not kraken_dry_run:
            from risk.human_gate import assert_live_session_active  # noqa: PLC0415

            assert_live_session_active()

        broker = KrakenBroker(dry_run=kraken_dry_run)
        # All real-broker fills get tagged as calibration. Promotion to full
        # 'live' is a manual operator action after the first 30 trades (S-55).
        trade_mode = LIVE_CALIBRATION_MODE
    elif broker_name == "paper":
        broker = PaperBroker(
            get_price=lambda s: marks.get(s, 0.0),
            slippage_bps=slippage_bps,
            commission_bps=commission_bps,
        )
        trade_mode = PAPER_MODE
    else:
        raise ValueError(f"unknown TRADERBOT_BROKER={broker_name!r}. Known: 'paper', 'kraken'")
    log = DecisionLog(log_path)
    # Risk-cap selection by trade_mode:
    #   paper             → default RiskCaps (loose; paper can't lose real money)
    #   live_calibration  → live_calibration_caps(initial_cash) — first 30 trades
    #   live              → live_full_caps(initial_cash) — post-calibration
    if trade_mode == LIVE_CALIBRATION_MODE:
        from risk.caps import live_calibration_caps  # noqa: PLC0415

        caps = live_calibration_caps(initial_cash_usd=initial_cash)
    elif trade_mode == "live":  # full-live promotion (manual operator action)
        from risk.caps import live_full_caps  # noqa: PLC0415

        caps = live_full_caps(initial_cash_usd=initial_cash)
    else:
        caps = RiskCaps()
    executor = Executor(
        broker=broker,
        log=log,
        strategy_id=strategy_entry.id,
        initial_cash=initial_cash,
        caps=caps,
        risk_pct=risk_pct,
        trade_mode=trade_mode,
    )

    # Auto-backup the decision log at every loop start. Cheap (file copy of a
    # small SQLite DB), idempotent, and gives us a rollback point if the live
    # session corrupts state. Failures here must NOT block startup — backup is
    # nice-to-have, not blocking.
    try:
        from tools.backup import backup_decision_log  # noqa: PLC0415

        backup_decision_log(log_path)
    except Exception as e:  # noqa: BLE001
        print(f"WARN: decision log backup failed at startup: {type(e).__name__}: {e}")

    # Reconcile-on-startup (P-11): pull broker positions/orders, compare to log.
    # Paper: always trivially clean (broker boots empty + log walked from scratch).
    # Live: a mismatch means a previous run left state we can't account for —
    # halt new entries via the kill switch and let the operator resolve manually.
    from execution.reconcile import reconcile  # noqa: PLC0415

    rec = reconcile(broker, log.all())
    log.append(
        DecisionEvent(
            timestamp_ms=int(time.time() * 1000),
            event_type="reconcile",
            symbol=symbol,
            strategy_id=strategy_entry.id,
            rationale=rec.summary(),
            metadata={
                "is_clean": rec.is_clean,
                "broker_positions": len(rec.broker_positions),
                "log_positions": len(rec.log_positions),
                "open_orders": rec.open_orders_count,
                "mismatches": [
                    {"symbol": m.symbol, "broker_qty": m.broker_qty, "log_qty": m.log_qty}
                    for m in rec.mismatches
                ],
                "mode": trade_mode,
            },
        )
    )
    if not rec.is_clean and trade_mode != PAPER_MODE:
        # Hard fail in live mode — refuse to start with broker/log divergence.
        # Operator must investigate and either: cancel the orphan orders, manually
        # log the missing trades into decision_log, or restart with a fresh log.
        raise RuntimeError(
            f"reconcile FAILED in {trade_mode} mode — refusing to start. "
            f"{rec.summary()}. Investigate before restarting."
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
        strategy_fn=strategy_entry.fn,
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
        "strategy": f"{strategy_entry.label} ({strategy_entry.id})",
        "broker": f"{broker_name} (mode={trade_mode})",
        "telegram": "configured" if notifier.configured else "not configured (silent)",
    }
    return loop, config


def main() -> None:
    loop, cfg = build_from_env()
    print("=" * 56)
    print("   🐺 Wolf Of Vibe Street — live loop (paper mode)")
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
