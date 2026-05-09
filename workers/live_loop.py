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
        symbol: str | list[str],
        timeframe: str,
        executor: Executor,
        marks: dict[str, float],
        *,
        exchange: str = "binance",
        client: OHLCVClient | None = None,
        clock_ms: Callable[[], int] | None = None,
        poll_interval_s: float = 30.0,
        strategy_fn: Callable[..., list[Signal]] | None = None,
        strategy_fn_by_symbol: dict[str, Callable[..., list[Signal]]] | None = None,
        kill_switch_path: Path = DEFAULT_KILL_SWITCH_PATH,
        notifier: Notifier | None = None,
        heartbeat_interval_s: float = 3600.0,
        reconcile_interval_s: float = 4 * 3600.0,
    ):
        if timeframe not in TIMEFRAME_MS:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}")
        # Accept either a single symbol (back-compat) or a list. Internally always
        # a list — single-symbol callers see no semantic difference.
        if isinstance(symbol, str):
            self.symbols: list[str] = [symbol]
        else:
            self.symbols = list(symbol)
        if not self.symbols:
            raise ValueError("Need at least one symbol")
        # `self.symbol` kept for back-compat (some places like log heartbeat reference it)
        self.symbol = self.symbols[0]
        self.timeframe = timeframe
        self.executor = executor
        self.marks = marks
        self.exchange = exchange
        self.client = client
        self.clock = clock_ms or (lambda: int(time.time() * 1000))
        self.poll_interval_s = poll_interval_s
        self.strategy_fn = strategy_fn or generate_signals
        # Per-symbol strategy override map. Symbols not in the map fall back to
        # `self.strategy_fn`. Empty dict ⇒ effectively single-strategy behavior.
        self.strategy_fn_by_symbol: dict[str, Callable[..., list[Signal]]] = (
            strategy_fn_by_symbol or {}
        )
        self.kill_switch_path = kill_switch_path
        self.notifier: Notifier = notifier or NoOpNotifier()
        self.heartbeat_interval_s = heartbeat_interval_s
        self.reconcile_interval_s = reconcile_interval_s
        # Per-symbol state — parquet path + last-processed timestamp tracked independently.
        self.parquet_paths: dict[str, Path] = {
            s: bars_path(exchange, s, timeframe) for s in self.symbols
        }
        # Seed checkpoint from the decision log so restarts don't re-process bars
        # we already saw. Without this, every loop start replays the last 10 fetched
        # bars and re-fires signals/orders, polluting the log + tripping reconcile.
        self._last_processed_ts: dict[str, int | None] = {
            s: self.executor.log.latest_signal_ts(s) for s in self.symbols
        }
        self._tf_ms = TIMEFRAME_MS[timeframe]
        self._kill_alerted = False
        self._last_heartbeat_ms: int | None = None
        self._last_reconcile_ms: int | None = None

    def _tick_symbol(self, sym: str, now_ms: int) -> int:
        """Process newly-closed bars for one symbol. Returns count processed."""
        recent = fetch_ohlcv(sym, timeframe=self.timeframe, limit=10, client=self.client)
        if not recent:
            return 0

        last = self._last_processed_ts.get(sym)
        new_bars = [
            b
            for b in recent
            if (last is None or b["timestamp_ms"] > last)
            and b["timestamp_ms"] + self._tf_ms <= now_ms
        ]
        if not new_bars:
            return 0

        save_bars(new_bars, self.parquet_paths[sym])

        all_bars = load_bars(self.parquet_paths[sym])
        df = bars_to_df(all_bars)
        if df.empty:
            return 0
        # Pick per-symbol strategy override if configured, otherwise the global default.
        strategy = self.strategy_fn_by_symbol.get(sym, self.strategy_fn)
        signals = strategy(df, symbol=sym)
        ts_to_idx = {int(t): i for i, t in enumerate(df["timestamp_ms"].tolist())}

        for new in new_bars:
            idx = ts_to_idx.get(new["timestamp_ms"])
            if idx is None:
                continue
            self.marks[sym] = float(new["close"])
            bar = Bar(
                timestamp_ms=new["timestamp_ms"],
                high=float(new["high"]),
                low=float(new["low"]),
                close=float(new["close"]),
            )
            self.executor.on_bar(signals[idx], bar)
            self._last_processed_ts[sym] = new["timestamp_ms"]

        return len(new_bars)

    def tick(self) -> int:
        """Process all newly-closed bars across ALL symbols. Returns total count.

        Per-symbol exceptions are caught + logged + notified so a single bad symbol
        doesn't stop others. The exception bubbles up too — `run()` notifies on it.
        """
        now_ms = self.clock()
        total = 0
        first_error: Exception | None = None
        for sym in self.symbols:
            try:
                total += self._tick_symbol(sym, now_ms)
            except Exception as e:
                self.executor.log.append(
                    DecisionEvent(
                        timestamp_ms=now_ms,
                        event_type="order_rejected",
                        symbol=sym,
                        strategy_id=self.executor.strategy_id,
                        rationale=f"tick_error: {type(e).__name__}: {e}",
                    )
                )
                if first_error is None:
                    first_error = e
        if first_error is not None:
            # Re-raise so run()'s outer handler notifies the operator. Other
            # symbols already processed — only the first failure surfaces.
            raise first_error
        return total

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

            # Heartbeat notification removed 2026-05-04 per operator request.
            # The 6×/day daily-summary cron is the alive-signal now; hourly
            # heartbeat pings on Telegram were noise. Field still kept on the
            # instance for tests/debugging but never triggers a notify.

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
                    # Reconcile mismatches are logged to the decision log + surfaced in the
                    # daily summary + dashboard. Telegram-noise removed 2026-05-04 per
                    # operator request — only critical errors should ping the phone.
                except Exception as e:  # noqa: BLE001
                    # Reconcile *crashing* is rare and serious — keep this Telegram alert.
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


def _build_conviction_context_fn(log, strategy_id: str):
    """Returns a function(signal, bar) → context dict for ConvictionMultiplier.
    Lazily pulls news + per-strategy stats so the LLM has fresh context on
    every entry. Defined here (not in Executor) to keep execution layer free
    of UI/news imports."""
    from data.news_store import NewsStore  # noqa: PLC0415
    from features.regime import detect_regime  # noqa: PLC0415

    news_store = NewsStore()

    def _ctx_fn(signal, bar) -> dict:
        from data.store import bars_path, load_bars  # noqa: PLC0415
        from features.compute import bars_to_df  # noqa: PLC0415

        ctx: dict = {}
        # News
        try:
            ctx["news_summary"] = news_store.summary(signal.symbol, window_h=24)
            recent = news_store.recent(signal.symbol, window_h=24, limit=5)
            ctx["news_headlines"] = [r["headline"] for r in recent]
        except Exception:  # noqa: BLE001
            ctx["news_summary"] = None
            ctx["news_headlines"] = []
        # Regime from 1h bars
        try:
            bars_p = bars_path("binance", signal.symbol, "1h")
            if bars_p.exists():
                df = bars_to_df(load_bars(bars_p))
                if len(df) >= 200:
                    regimes = detect_regime(df, trend_period=200)
                    ctx["regime"] = str(regimes["trend"].iloc[-1])
        except Exception:  # noqa: BLE001
            ctx["regime"] = None
        # Reward/risk ratio
        if signal.stop is not None and signal.target is not None and bar.close > signal.stop:
            risk = bar.close - signal.stop if signal.side == "buy" else signal.stop - bar.close
            reward = signal.target - bar.close if signal.side == "buy" else bar.close - signal.target
            if risk > 0:
                ctx["rr_ratio"] = reward / risk
        return ctx

    return _ctx_fn


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
    # TRADERBOT_SYMBOLS (plural, comma-separated) takes precedence for multi-asset
    # trading. Falls back to TRADERBOT_SYMBOL (singular) for back-compat.
    symbols_env = os.environ.get("TRADERBOT_SYMBOLS", "").strip()
    if symbols_env:
        symbols = [s.strip() for s in symbols_env.split(",") if s.strip()]
    else:
        symbols = [_env("TRADERBOT_SYMBOL")]
    symbol = symbols[0]  # primary, for back-compat references
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

    # Per-symbol strategy override (added 2026-05-02). Format:
    #   TRADERBOT_STRATEGY_PER_SYMBOL=BTC/USDT:union_meanrev_breakout,ETH/USDT:regime_aware_dipbuy
    # Symbols not in the map fall back to the global TRADERBOT_STRATEGY.
    # Walk-forward shows different symbols respond best to different strategies
    # (BTC/ADA/LINK like union, ETH/SOL/AVAX like dipbuy) — single global
    # strategy across all symbols leaves edge on the table.
    per_sym_str = os.environ.get("TRADERBOT_STRATEGY_PER_SYMBOL", "").strip()
    strategy_fn_by_symbol: dict[str, Callable[..., list[Signal]]] = {}
    if per_sym_str:
        for pair in per_sym_str.split(","):
            pair = pair.strip()
            if ":" not in pair:
                continue
            sym, sid = (p.strip() for p in pair.split(":", 1))
            if sym and sid:
                try:
                    strategy_fn_by_symbol[sym] = strategy_by_id(sid).fn
                except KeyError:
                    pass  # unknown strategy id → silent skip, fall back to global
    heartbeat_s = float(_env("TRADERBOT_HEARTBEAT_INTERVAL_S"))
    slippage_bps = float(_env("TRADERBOT_SLIPPAGE_BPS"))
    commission_bps = float(_env("TRADERBOT_COMMISSION_BPS"))

    marks: dict[str, float] = {}
    # Broker selection — paper by default; KrakenBroker only when LIVE_TRADING=true
    # AND TRADERBOT_BROKER=kraken. The is_live_trading_enabled() check is
    # belt-and-suspenders — KrakenBroker's own constructor also asserts it.
    from risk.live_gate import (  # noqa: PLC0415
        LIVE_CALIBRATION_MODE,
        LIVE_MODE,
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
        # Default: real-broker fills tagged as calibration. After 30 trades the
        # operator sets TRADERBOT_TRADE_MODE=live in `.env` (dashboard button)
        # and restarts — `live_full_caps` applies.
        trade_mode = LIVE_CALIBRATION_MODE
        tm = os.environ.get("TRADERBOT_TRADE_MODE", "").strip().lower()
        if tm == LIVE_MODE:
            trade_mode = LIVE_MODE
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
    # Paper broker is in-memory only — without restoring from the log, every
    # restart leaves the broker thinking it has 0 of everything while the log
    # remembers all open positions, breaking reconcile. Walk fills once on
    # startup and rebuild the position + COID cache.
    if isinstance(broker, PaperBroker):
        broker.restore_from_fills(log.all())
    # Risk-cap selection by trade_mode:
    #   paper             → default RiskCaps (loose; paper can't lose real money)
    #   live_calibration  → live_calibration_caps(initial_cash) — first 30 trades
    #   live              → live_full_caps(initial_cash) — post-calibration
    if trade_mode == LIVE_CALIBRATION_MODE:
        from risk.caps import live_calibration_caps  # noqa: PLC0415

        caps = live_calibration_caps(initial_cash_usd=initial_cash)
    elif trade_mode == LIVE_MODE:  # full-live promotion (manual operator action)
        from risk.caps import live_full_caps  # noqa: PLC0415

        caps = live_full_caps(initial_cash_usd=initial_cash)
    else:
        # Paper: use sized-by-equity caps so a tight stop can't put 60% of the
        # account into a single position (real bug found 2026-05-03).
        from risk.caps import paper_caps  # noqa: PLC0415

        caps = paper_caps(initial_cash_usd=initial_cash)
    # Optional Phase 4 LLM conviction multiplier. Activated by
    # TRADERBOT_USE_CONVICTION=true (requires ANTHROPIC_API_KEY). Wraps every
    # entry signal in a sizing multiplier between 0.3 and 1.5 based on news
    # sentiment + regime + recent strategy P&L. Never vetoes.
    use_conviction = os.environ.get("TRADERBOT_USE_CONVICTION", "").strip().lower() == "true"
    conviction_evaluator = None
    conviction_context_fn = None
    if use_conviction:
        from agents.conviction import ConvictionMultiplier  # noqa: PLC0415

        if not ConvictionMultiplier.is_configured():
            raise RuntimeError(
                "TRADERBOT_USE_CONVICTION=true but ANTHROPIC_API_KEY is not set."
            )
        conviction_evaluator = ConvictionMultiplier()
        conviction_context_fn = _build_conviction_context_fn(log, strategy_entry.id)

    executor = Executor(
        broker=broker,
        log=log,
        strategy_id=strategy_entry.id,
        initial_cash=initial_cash,
        caps=caps,
        risk_pct=risk_pct,
        trade_mode=trade_mode,
        conviction_evaluator=conviction_evaluator,
        conviction_context_fn=conviction_context_fn,
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

    # Real-time fill alerts with batching to avoid Telegram spam when multiple
    # symbols fill in the same tick. Buffer up incoming alerts; flush as a
    # single combined message after a brief debounce window. With 5+ ensemble
    # symbols on the same bar this would otherwise fire 5 separate pings; one
    # message with 5 lines is dramatically less noisy.
    import threading  # noqa: PLC0415

    _alert_buffer: list[str] = []
    _alert_lock = threading.Lock()
    _flush_timer: list[threading.Timer | None] = [None]
    FLUSH_DELAY_S = 5.0  # gather fills within 5s into one message

    def _flush_alerts() -> None:
        with _alert_lock:
            if not _alert_buffer:
                return
            text = "\n".join(_alert_buffer)
            _alert_buffer.clear()
            _flush_timer[0] = None
        if notifier.configured:
            notifier.notify("INFO", "Fill", text)

    def _fill_alert(side: str, symbol: str, price: float, pnl_pct: float | None) -> None:
        short = symbol.split("/")[0] if "/" in symbol else symbol
        if side in ("long", "buy"):
            line = f"Buy {short} @ ${price:,.4f}"
        elif side == "short":
            line = f"Short {short} @ ${price:,.4f}"
        elif side == "cover":
            sign = "+" if (pnl_pct or 0) >= 0 else ""
            pct_part = f" {sign}{pnl_pct:.2f}%" if pnl_pct is not None else ""
            line = f"COVERED {short}{pct_part} @ ${price:,.4f}"
        else:  # sell (long close)
            sign = "+" if (pnl_pct or 0) >= 0 else ""
            pct_part = f" {sign}{pnl_pct:.2f}%" if pnl_pct is not None else ""
            line = f"SOLD {short}{pct_part} @ ${price:,.4f}"
        with _alert_lock:
            _alert_buffer.append(line)
            if _flush_timer[0] is None:
                _flush_timer[0] = threading.Timer(FLUSH_DELAY_S, _flush_alerts)
                _flush_timer[0].daemon = True
                _flush_timer[0].start()

    executor._on_fill = _fill_alert

    # LLM filter — wraps the base strategy so every BUY signal goes through
    # Claude before the executor sees it. Rejected buys become HOLD with
    # the rejection reason in the decision log. Sells/holds pass through
    # unchanged. Activated by TRADERBOT_USE_LLM_FILTER=true; requires
    # ANTHROPIC_API_KEY in env (raises with clear error otherwise).
    use_llm_filter = os.environ.get("TRADERBOT_USE_LLM_FILTER", "").strip().lower() == "true"
    actual_strategy_fn = strategy_entry.fn
    llm_filter_label = "off"
    if use_llm_filter:
        from agents.llm_evaluator import ClaudeEvaluator  # noqa: PLC0415
        from strategies.llm_filtered import make_llm_filtered_strategy  # noqa: PLC0415

        if not ClaudeEvaluator.is_configured():
            raise RuntimeError(
                "TRADERBOT_USE_LLM_FILTER=true but ANTHROPIC_API_KEY is not set. "
                "Add your Anthropic API key to .env, or unset the filter flag."
            )
        try:
            llm_threshold = float(os.environ.get("TRADERBOT_LLM_THRESHOLD", "0.3"))
        except ValueError:
            llm_threshold = 0.3
        evaluator = ClaudeEvaluator()
        actual_strategy_fn = make_llm_filtered_strategy(
            strategy_entry.fn, evaluator, threshold=llm_threshold
        )
        llm_filter_label = f"on (threshold={llm_threshold:+.2f})"

    loop = LiveLoop(
        symbol=symbols if len(symbols) > 1 else symbol,
        timeframe=timeframe,
        executor=executor,
        marks=marks,
        poll_interval_s=poll_interval_s,
        notifier=notifier,
        heartbeat_interval_s=heartbeat_s,
        strategy_fn=actual_strategy_fn,
        strategy_fn_by_symbol=strategy_fn_by_symbol or None,
    )

    config = {
        "symbol": ",".join(symbols) if len(symbols) > 1 else symbol,
        "timeframe": timeframe,
        "log_path": str(log_path),
        "initial_cash": f"${initial_cash:,.2f}",
        "risk_pct": f"{risk_pct * 100:.2f}%",
        "poll_interval_s": str(poll_interval_s),
        "slippage_bps": str(slippage_bps),
        "commission_bps": str(commission_bps),
        "heartbeat_interval_s": str(heartbeat_s),
        "strategy": f"{strategy_entry.label} ({strategy_entry.id})",
        "llm_filter": llm_filter_label,
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

    # Two-way Telegram: listen for /status, /pnl, /trades, /help from the operator's
    # phone. Only spawned if telegram is configured. Daemon thread — dies with the loop.
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if tg_token and tg_chat:
        try:
            from pathlib import Path  # noqa: PLC0415

            from tools.telegram_commands import TelegramCommandListener  # noqa: PLC0415

            listener = TelegramCommandListener(
                token=tg_token,
                chat_id=tg_chat,
                log_path=Path(cfg["log_path"]),
                kill_switch_path=Path(os.environ.get("TRADERBOT_KILL_SWITCH_PATH", "data/state/KILL_SWITCH")),
                initial_cash=float(cfg.get("initial_cash", "10000").replace("$", "").replace(",", "")),
            )
            listener.start()
            print("  telegram cmds:          listening for /help /status /pnl /trades")
        except Exception as e:  # noqa: BLE001
            print(f"  telegram cmds:          failed to start listener: {e}")

    try:
        loop.run(max_iterations=max_iters)
    except KeyboardInterrupt:
        print("\nShutdown requested. Decision log is on disk at:")
        print(f"  {cfg['log_path']}")
        print("Open the dashboard with: uv run python -m web.main  (http://localhost:8000)")


if __name__ == "__main__":
    main()
