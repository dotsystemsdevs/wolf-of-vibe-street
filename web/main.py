"""FastAPI dashboard for traderbot.

Run: `uv run python -m web.main`. Serves on http://127.0.0.1:8000.
Replacement for `ui/dashboard.py` (Streamlit). Reuses `ui/views.py` data shaping.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import csv
import io

import pandas as pd
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env into os.environ so all env reads (TRADERBOT_*, TELEGRAM_*, etc.) work.
# FastAPI doesn't auto-load .env the way the live-loop subprocess does via loop_control.
try:
    from tools.env_config import read_env as _read_env  # noqa: E402
    for _k, _v in _read_env().items():
        os.environ.setdefault(_k, _v)
except Exception:  # noqa: BLE001
    pass

from memory.decision_log import DecisionLog  # noqa: E402
from risk.caps import DEFAULT_KILL_SWITCH_PATH, kill_switch_active  # noqa: E402
from risk.human_gate import (  # noqa: E402
    DEFAULT_TOKEN_PATH,
    LIVE_CONFIRMATION_PHRASE,
    activate_live_session,
    deactivate_live_session,
    get_session_state,
)
from tools import cron_manage, env_config, loop_control  # noqa: E402
from tools.notifier import TelegramNotifier  # noqa: E402
from ui.views import (  # noqa: E402
    day_pnl,
    equity_curve,
    open_positions,
    soak_health,
    summary,
    trades_dataframe,
)

DEFAULT_DB_PATH = Path("data/decision_log/traderbot.db")
DASHBOARD_BUILD = "2026-05-03-alerts-multi"

NAV_ITEMS = [
    {"key": "desk", "path": "/", "label": "DESK"},
    {"key": "compare", "path": "/compare", "label": "COMPARE"},
    {"key": "tape", "path": "/tape", "label": "TAPE"},
    {"key": "settings", "path": "/settings", "label": "SETTINGS"},
]

_TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
               "1h": 3600, "4h": 14400, "1d": 86400}

app = FastAPI(title="traderbot")
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _runtime_paths() -> tuple[Path, float, Path]:
    log_path = Path(os.environ.get("TRADERBOT_LOG_PATH", str(DEFAULT_DB_PATH)))
    initial_cash = float(os.environ.get("TRADERBOT_INITIAL_CASH", "10000"))
    kill_switch_path = Path(
        os.environ.get("TRADERBOT_KILL_SWITCH_PATH", str(DEFAULT_KILL_SWITCH_PATH))
    )
    return log_path, initial_cash, kill_switch_path


def _read_rows(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    log = DecisionLog(log_path)
    rows = log.all()
    log.close()
    return rows


def _compute_health(log_path: Path, kill_switch_path: Path) -> tuple[str, list[dict], int]:
    """Compute soak-health checks. Returns (worst_status, checks, error_count).

    Single source of truth for health — used by topbar, DESK, and SYSTEM so the
    error indicator shows the same thing on every page.
    """
    rows = _read_rows(log_path)
    loop_status = loop_control.status()
    timeframe = os.environ.get("TRADERBOT_TIMEFRAME", "1h")
    tf_seconds = _TF_SECONDS.get(timeframe, 3600)
    checks = soak_health(
        rows,
        bot_running=loop_status.running,
        kill_switch_on=kill_switch_active(kill_switch_path),
        now_ms=int(pd.Timestamp.now("UTC").timestamp() * 1000),
        expected_bar_seconds=tf_seconds,
        loop_started_at_ms=loop_status.started_at_ms,
    )
    worst = "ok"
    err_count = 0
    for c in checks:
        if c["status"] == "error":
            err_count += 1
            worst = "error"
        elif c["status"] == "warn" and worst != "error":
            worst = "warn"
    return worst, checks, err_count


def _topbar_ctx(kill_switch_path: Path) -> dict:
    """Mode chip, symbol, next bar, clock, status, uptime, health badge."""
    now = pd.Timestamp.now("UTC")
    loop_status = loop_control.status()
    kill_on = kill_switch_active(kill_switch_path)
    live_trading = os.environ.get("LIVE_TRADING", "false").strip().lower() == "true"

    # Compute health every time so topbar's red dot reflects truth across all pages.
    log_path, _, _ = _runtime_paths()
    worst, _, err_count = _compute_health(log_path, kill_switch_path)

    if not loop_status.running:
        status_label, status_cls = "OFF", "off"
    elif kill_on:
        status_label, status_cls = "IDLE", "idle"
    elif worst == "error":
        status_label, status_cls = "ERROR", "off"
    elif worst == "warn":
        status_label, status_cls = "WARN", "idle"
    else:
        status_label, status_cls = "RUN", "run"

    symbol = os.environ.get("TRADERBOT_SYMBOL", "BTC/USDT")
    timeframe = os.environ.get("TRADERBOT_TIMEFRAME", "1h")
    tf_seconds = _TF_SECONDS.get(timeframe, 3600)
    next_bar_in = tf_seconds - (int(now.timestamp()) % tf_seconds)
    nb_min, nb_sec = divmod(next_bar_in, 60)
    nb_h, nb_min = divmod(nb_min, 60)
    next_bar = f"{nb_h}h {nb_min:02d}m" if nb_h else f"{nb_min:02d}:{nb_sec:02d}"

    detail = ""
    if (
        loop_status.running
        and loop_status.pid is not None
        and loop_status.started_at_ms is not None
    ):
        uptime_s = int((now.timestamp() * 1000 - loop_status.started_at_ms) / 1000)
        m, s = divmod(uptime_s, 60)
        h, m = divmod(m, 60)
        up = f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"
        detail = f"{up} · PID {loop_status.pid}"

    return {
        "mode": "LIVE" if live_trading else "PAPER",
        "mode_cls": "live" if live_trading else "paper",
        "symbol": symbol,
        "timeframe": timeframe,
        "next_bar": next_bar,
        "clock": now.strftime("%H:%M:%S"),
        "status_label": status_label,
        "status_cls": status_cls,
        "status_detail": detail,
        "err_count": err_count,
        "worst": worst,
    }


def _base_ctx(request: Request, view: str, **extra) -> dict:
    _, _, kill_switch_path = _runtime_paths()
    ctx = {
        "request": request,
        "active_view": view,
        "nav_items": NAV_ITEMS,
        "topbar": _topbar_ctx(kill_switch_path),
        "build": DASHBOARD_BUILD,
        "project_name": _PROJECT_ROOT.name,
    }
    ctx.update(extra)
    return ctx


def _candles_and_markers(symbol: str, timeframe: str, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Load OHLC bars from the local Parquet cache and overlay buy/sell markers
    derived from the decision log. Returns ([candles], [markers]) for lightweight-charts.

    Both arrays are sorted ascending by time. Empty if the parquet doesn't exist yet.
    """
    candles: list[dict] = []
    markers: list[dict] = []
    try:
        from data.store import bars_path, load_bars  # noqa: PLC0415

        path = bars_path("binance", symbol, timeframe)
        if not path.exists():
            return [], []
        bars = load_bars(path)
        seen_t: set[int] = set()
        for b in bars:
            t = int(int(b["timestamp_ms"]) // 1000)
            while t in seen_t:
                t += 1
            seen_t.add(t)
            candles.append({
                "time": t,
                "open": float(b["open"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "close": float(b["close"]),
            })
    except Exception:  # noqa: BLE001
        return [], []

    if not candles:
        return [], []

    cand_min = candles[0]["time"]
    cand_max = candles[-1]["time"]
    mark_seen: set[tuple[int, str]] = set()
    for r in rows:
        if r.get("event_type") != "order_filled":
            continue
        if r.get("symbol") != symbol:
            continue
        side = r.get("side", "")
        if side not in ("buy", "sell"):
            continue
        t = int(int(r["timestamp_ms"]) // 1000)
        # Snap markers into the chart's time range so they always render
        if t < cand_min or t > cand_max:
            continue
        key = (t, side)
        if key in mark_seen:
            continue
        mark_seen.add(key)
        price = float(r.get("price") or 0.0)
        markers.append({
            "time": t,
            "position": "belowBar" if side == "buy" else "aboveBar",
            "color": "#22c55e" if side == "buy" else "#ef4444",
            "shape": "arrowUp" if side == "buy" else "arrowDown",
            "text": ("B " if side == "buy" else "S ") + (f"{price:.0f}" if price > 100 else f"{price:.2f}"),
        })
    markers.sort(key=lambda m: m["time"])
    return candles, markers


@app.get("/", response_class=HTMLResponse)
def desk(request: Request):
    log_path, initial_cash, kill_switch_path = _runtime_paths()
    rows = _read_rows(log_path)

    s = summary(rows, initial_cash=initial_cash)
    eq_df = equity_curve(rows, initial_cash=initial_cash)
    positions = open_positions(rows)
    trades = trades_dataframe(rows)

    now_ms = int(pd.Timestamp.now("UTC").timestamp() * 1000)
    timeframe = os.environ.get("TRADERBOT_TIMEFRAME", "1h")
    strategy_id = os.environ.get("TRADERBOT_STRATEGY") or os.environ.get("TRADERBOT_STRATEGY_ID") or "baseline_ema_cross"

    # Multi-symbol: TRADERBOT_SYMBOLS takes precedence, falls back to TRADERBOT_SYMBOL.
    symbols_env = os.environ.get("TRADERBOT_SYMBOLS", "").strip()
    if symbols_env:
        all_symbols = [s.strip() for s in symbols_env.split(",") if s.strip()]
    else:
        all_symbols = [os.environ.get("TRADERBOT_SYMBOL", "BTC/USDT")]
    symbol = all_symbols[0]  # primary — used for legacy single-symbol references

    # Build per-symbol candlestick + marker data for the price-chart grid.
    price_charts = []
    for sym in all_symbols:
        c, m = _candles_and_markers(sym, timeframe, rows)
        if c:
            # Last 3 events for this symbol (any event_type)
            sym_events = [r for r in rows if r.get("symbol") == sym]
            recent_3 = []
            for r in sym_events[-3:][::-1]:
                ts = pd.to_datetime(int(r["timestamp_ms"]), unit="ms", utc=True)
                recent_3.append({
                    "time": ts.strftime("%H:%M"),
                    "event": r["event_type"],
                    "side": r.get("side") or "",
                    "rationale": str(r.get("rationale") or "")[:60],
                })
            price_charts.append({
                "symbol": sym,
                "slug": sym.replace("/", "_"),
                "candles": c,
                "markers": m,
                "recent": recent_3,
            })

    # Bot pulse — shows the user the bot IS active even when not trading.
    # Pulls last signal, signal rate, current EMA state for context.
    pulse = {"signals_total": 0, "holds": 0, "buys": 0, "sells": 0, "last_signal": None,
             "last_signal_min_ago": None, "ema_fast": None, "ema_slow": None,
             "ema_diff": None, "ema_diff_pct": None, "close": None, "next_bar_min": None}
    sigs = [r for r in rows if r["event_type"] == "signal"]
    pulse["signals_total"] = len(sigs)
    pulse["holds"] = sum(1 for r in sigs if (r.get("side") or "") == "hold")
    pulse["buys"] = sum(1 for r in sigs if (r.get("side") or "") == "buy")
    pulse["sells"] = sum(1 for r in sigs if (r.get("side") or "") == "sell")
    if sigs:
        last_sig = sigs[-1]
        pulse["last_signal"] = last_sig.get("side") or "hold"
        last_ms = int(last_sig["timestamp_ms"])
        pulse["last_signal_min_ago"] = int((now_ms - last_ms) / 60000)
    # Multi-symbol regime distribution — replaces the old "Trend (single symbol)"
    # pulse card. Counts how many symbols are currently in uptrend/sideways/downtrend
    # so the operator sees the macro-state at a glance instead of one symbol's EMA gap.
    pulse["regimes"] = {"uptrend": 0, "sideways": 0, "downtrend": 0}
    try:
        from data.store import bars_path as _bp, load_bars as _lb  # noqa: PLC0415
        from features.compute import bars_to_df as _b2df  # noqa: PLC0415
        from features.regime import detect_regime as _detect  # noqa: PLC0415

        for sym_r in all_symbols:
            bars_p = _bp("binance", sym_r, timeframe)
            if not bars_p.exists():
                continue
            bars_r = _lb(bars_p)
            if not bars_r:
                continue
            df_r = _b2df(bars_r)
            try:
                regimes = _detect(df_r, trend_period=200)
                if not regimes.empty:
                    last_regime = str(regimes["trend"].iloc[-1])
                    if last_regime in pulse["regimes"]:
                        pulse["regimes"][last_regime] += 1
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass

    # Per-symbol strategy mapping for display. Reads the same env vars as
    # workers.live_loop.build_from_env so dashboard reflects what's actually live.
    per_sym_raw = os.environ.get("TRADERBOT_STRATEGY_PER_SYMBOL", "").strip()
    strategy_by_symbol: dict[str, str] = {}
    if per_sym_raw:
        for pair in per_sym_raw.split(","):
            pair = pair.strip()
            if ":" in pair:
                k, v = (p.strip() for p in pair.split(":", 1))
                if k and v:
                    strategy_by_symbol[k] = v
    # Fill in defaults for symbols not in the per-symbol map
    for sym_d in all_symbols:
        strategy_by_symbol.setdefault(sym_d, strategy_id)
    # Next bar countdown
    tf_seconds = _TF_SECONDS.get(timeframe, 3600)
    next_bar_in_s = tf_seconds - (int(now_ms / 1000) % tf_seconds)
    pulse["next_bar_min"] = next_bar_in_s // 60

    current_equity = float(eq_df.iloc[-1]["equity"]) if not eq_df.empty else initial_cash
    current_cash = float(eq_df.iloc[-1]["cash"]) if not eq_df.empty else initial_cash
    delta_eq = current_equity - initial_cash
    total_return_pct = (delta_eq / initial_cash * 100) if initial_cash > 0 else 0.0
    today = day_pnl(rows, now_ms=now_ms)

    kpis = {
        "equity": current_equity,
        "equity_delta": delta_eq,
        "equity_pct": total_return_pct,
        "cash": current_cash,
        "cash_delta": current_cash - initial_cash,
        "today_pnl": today,
        "open_positions": len(positions),
        "max_positions": s.get("trades", 0),
        "trades": s["trades"],
        "win_rate": s["win_rate"] * 100,
        "wins": s["wins"],
        "losses": s["losses"],
    }

    # Pro-trader performance metrics — pulled from the equity curve + trades.
    # Sharpe is annualized using the bar timeframe; profit factor + expectancy
    # come from realized round-trip trades. All NaN-safe → "—" when no data.
    from backtest.metrics import (  # noqa: PLC0415
        equity_returns as _eq_returns, max_drawdown as _max_dd, sharpe as _sharpe,
    )
    perf: dict[str, float | None] = {
        "sharpe": None, "max_dd_pct": None, "profit_factor": None,
        "expectancy": None, "avg_r": None, "exposure_pct": None, "fees_total": None,
    }
    if not eq_df.empty and len(eq_df) > 1:
        eq_series = eq_df.set_index("timestamp_ms")["equity"]
        rets = _eq_returns(eq_series)
        # ~8760 bars per year for 1h, 35040 for 15m.
        periods = {"1m": 525600, "5m": 105120, "15m": 35040, "30m": 17520,
                   "1h": 8760, "4h": 2190, "1d": 365}.get(timeframe, 8760)
        sh = _sharpe(rets, periods_per_year=periods)
        perf["sharpe"] = sh if sh == sh else None  # NaN-check
        perf["max_dd_pct"] = float(_max_dd(eq_series)) * 100.0
    if not trades.empty:
        pnls = trades["pnl"].astype(float).tolist()
        wins_pnl = [p for p in pnls if p > 0]
        losses_pnl = [p for p in pnls if p < 0]
        if losses_pnl:
            perf["profit_factor"] = sum(wins_pnl) / abs(sum(losses_pnl))
        elif wins_pnl:
            perf["profit_factor"] = float("inf")
        perf["expectancy"] = sum(pnls) / len(pnls)
        rs = trades["r_multiple"].dropna().astype(float).tolist()
        if rs:
            perf["avg_r"] = sum(rs) / len(rs)
        perf["fees_total"] = float(trades["fees"].astype(float).sum())
    # Total exposure = open notional / equity (single source of truth for
    # "how much of my capital is at risk right now")
    if positions and current_equity > 0:
        notional = sum(p["qty"] * p["last_price"] for p in positions)
        perf["exposure_pct"] = notional / current_equity * 100.0

    # Equity curve as lightweight-charts data points: [{time: epoch_seconds, value: equity}, ...].
    # lightweight-charts requires strictly increasing time; collapse same-second entries by last.
    equity_data: list[dict] = []
    seen: set[int] = set()
    if not eq_df.empty:
        for ts_ms, eq in zip(eq_df["timestamp_ms"].tolist(), eq_df["equity"].tolist(), strict=False):
            t = int(int(ts_ms) // 1000)
            while t in seen:
                t += 1
            seen.add(t)
            equity_data.append({"time": t, "value": float(eq)})

    # Soak health checks for the critical-error banner (full list lives on /system).
    worst, health_checks, _ = _compute_health(log_path, kill_switch_path)
    health_label = {"ok": "All clear", "warn": "Worth a look", "error": "Needs work"}[worst]

    # Recent trades — last 12, newest first.
    recent_trades = []
    if not trades.empty:
        for _, t in trades.tail(12)[::-1].iterrows():
            holding_min = int(t.get("holding_ms", 0)) // 60_000
            r_mult = t.get("r_multiple")
            recent_trades.append({
                "ts": pd.to_datetime(int(t["exit_ts"]), unit="ms", utc=True).strftime("%m-%d %H:%M"),
                "symbol": t["symbol"],
                "qty": float(t["qty"]),
                "entry": float(t["entry_price"]),
                "exit": float(t["exit_price"]),
                "pnl": float(t["pnl"]),
                "pct": float(t["return_pct"]) * 100,
                "reason": str(t.get("exit_reason") or "")[:40],
                "holding_min": holding_min,
                "r_multiple": float(r_mult) if r_mult is not None and r_mult == r_mult else None,
            })

    # Enrich open positions with stop/target distance + age
    enriched_positions = []
    for p in positions:
        last = float(p["last_price"]) or 0.0
        entry = float(p["avg_entry"]) or 0.0
        stop = p.get("stop")
        target = p.get("target")
        age_min = (now_ms - int(p.get("entry_ts") or 0)) // 60_000 if p.get("entry_ts") else None
        # Distance to stop/target as % of current price
        dist_stop_pct = ((last - float(stop)) / last * 100.0) if stop and last else None
        dist_target_pct = ((float(target) - last) / last * 100.0) if target and last else None
        enriched_positions.append({
            **p,
            "notional": p["qty"] * last,
            "pnl_pct": ((last / entry - 1.0) * 100.0) if entry else 0.0,
            "dist_stop_pct": dist_stop_pct,
            "dist_target_pct": dist_target_pct,
            "age_min": age_min,
        })

    # Activity feed — last 15 events (any type), newest first. Live pulse of the bot.
    activity: list[dict] = []
    for r in rows[-15:][::-1]:
        ts = pd.to_datetime(int(r["timestamp_ms"]), unit="ms", utc=True)
        activity.append({
            "id": r["id"],
            "time": ts.strftime("%H:%M:%S"),
            "date": ts.strftime("%m-%d"),
            "event": r["event_type"],
            "symbol": r.get("symbol") or "",
            "side": r.get("side") or "",
            "price": r.get("price"),
            "qty": r.get("quantity"),
            "rationale": str(r.get("rationale") or "")[:80],
        })

    return templates.TemplateResponse(
        request,
        "desk.html",
        _base_ctx(
            request,
            "desk",
            kpis=kpis,
            perf=perf,
            positions=enriched_positions,
            equity_data=equity_data,
            price_charts=price_charts,
            symbol=symbol,
            all_symbols=all_symbols,
            strategy_by_symbol=strategy_by_symbol,
            timeframe=timeframe,
            health_checks=health_checks,
            health_worst=worst,
            health_label=health_label,
            recent_trades=recent_trades,
            activity=activity,
            pulse=pulse,
            strategy_id=strategy_id,
            no_data=not rows,
            log_path=str(log_path),
            initial_cash=initial_cash,
        ),
    )


@app.get("/topbar", response_class=HTMLResponse)
def topbar_partial(request: Request):
    """HTMX endpoint — returns only the topbar markup for periodic refresh."""
    _, _, kill_switch_path = _runtime_paths()
    return templates.TemplateResponse(
        request,
        "_topbar.html",
        {"topbar": _topbar_ctx(kill_switch_path)},
    )


def _stub_view(request: Request, view: str, title: str, blurb: str):
    return templates.TemplateResponse(
        request,
        "stub.html",
        _base_ctx(request, view, page_title=title, blurb=blurb),
    )


# Old paths kept as redirects so bookmarks survive — now point to settings.
from fastapi.responses import RedirectResponse as _Redir  # noqa: E402, PLC0415


@app.get("/go-live")
def go_live_redirect():
    return _Redir("/settings", status_code=308)


@app.get("/map")
def map_redirect():
    return _Redir("/settings", status_code=308)


@app.get("/system")
def system_redirect():
    return _Redir("/settings", status_code=308)


_compare_cache: dict[str, object] = {
    "results": None, "params": None, "error": None, "wf_reports": None,
}


@app.get("/compare", response_class=HTMLResponse)
def compare(request: Request):
    from backtest.compare import DEFAULT_SYMBOLS, STRATEGIES  # noqa: PLC0415

    results = _compare_cache.get("results")
    rows = []
    equity_series: list[dict] = []
    if results:
        from backtest.compare import rank_by_expectancy  # noqa: PLC0415

        ranked = rank_by_expectancy(results)
        for r in ranked:
            m = r.result.metrics
            strat_pct = m["total_return_pct"] * 100
            diff = strat_pct - r.buy_hold_return_pct
            pf = float(m.get("profit_factor", 0.0))
            pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
            rows.append({
                "symbol": r.symbol,
                "bars": r.bars,
                "trades": int(m["num_trades"]),
                "win_rate": m["win_rate"] * 100,
                "expectancy": float(m.get("expectancy", 0.0)),
                "pf": pf_str,
                "pf_val": pf,
                "strategy_pct": strat_pct,
                "buy_hold_pct": r.buy_hold_return_pct,
                "diff": diff,
                "sharpe": m["sharpe"],
                "max_dd": m["max_drawdown"] * 100,
            })

        # Build per-symbol equity curves for the multi-line overlay chart.
        # Normalize each curve to start at 100 so symbols with different
        # initial cash values can be compared on the same y-scale.
        for r in ranked:
            eq = r.result.equity_curve
            if eq is None or len(eq) == 0:
                continue
            base = float(eq.iloc[0]) if float(eq.iloc[0]) > 0 else 1.0
            seen_t: set[int] = set()
            data: list[dict] = []
            for ts_ms, val in zip(eq.index.tolist(), eq.values.tolist(), strict=False):
                t = int(int(ts_ms) // 1000)
                while t in seen_t:
                    t += 1
                seen_t.add(t)
                data.append({"time": t, "value": float(val) / base * 100.0})
            equity_series.append({"symbol": r.symbol, "data": data})

    return templates.TemplateResponse(
        request,
        "compare.html",
        _base_ctx(
            request, "compare",
            symbols_default=", ".join(DEFAULT_SYMBOLS),
            strategy_labels=[e.label for e in STRATEGIES.values()],
            params=_compare_cache.get("params"),
            rows=rows,
            equity_series=equity_series,
            wf_reports=_compare_cache.get("wf_reports"),
            error=_compare_cache.get("error"),
        ),
    )


@app.post("/compare/validate")
def compare_validate():
    """Run walk-forward validation on the current backtest results — verifies
    the strategy isn't regime-luck. Each symbol's full bar window is split into
    6 folds, strategy runs independently per fold, verdict aggregates."""
    from backtest.compare import ensure_backfill, strategy_by_label  # noqa: PLC0415
    from backtest.engine import BacktestConfig  # noqa: PLC0415
    from backtest.walk_forward import walk_forward  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    params = _compare_cache.get("params")
    if not params:
        _compare_cache["error"] = "Run a backtest first, then click Validate."
        return RedirectResponse("/compare", status_code=303)

    sym_list = [s.strip() for s in str(params["symbols"]).split(",") if s.strip()]
    days = int(params["days"])
    timeframe = str(params["timeframe"])
    strategy_label = str(params["strategy"])
    cfg = BacktestConfig(initial_cash=10_000.0, risk_pct=0.005, slippage_bps=5, commission_bps=10)

    reports: list[dict] = []
    try:
        entry = strategy_by_label(strategy_label)
        since_ms = int((_time.time() - days * 86400) * 1000)
        for sym in sym_list:
            df = ensure_backfill(sym, timeframe, since_ms)
            report = walk_forward(
                df,
                symbol=sym, timeframe=timeframe,
                strategy_label=strategy_label,
                strategy_fn=entry.fn,
                config=cfg,
                n_folds=6,
            )
            reports.append({
                "symbol": report.symbol,
                "timeframe": report.timeframe,
                "n_folds": report.n_folds,
                "verdict": report.verdict,
                "folds_pf_above_1": report.folds_pf_above_1,
                "folds_pf_below_05": report.folds_pf_below_05,
                "median_pf": report.median_pf,
                "median_sharpe": report.median_sharpe,
                "aggregate_return_pct": report.aggregate_return_pct,
                "folds": [
                    {
                        "i": f.fold_index + 1,
                        "trades": f.trades,
                        "win_rate": f.win_rate * 100,
                        "expectancy": f.expectancy,
                        "pf": f.profit_factor,
                        "sharpe": f.sharpe,
                        "sortino": f.sortino,
                        "max_dd": f.max_drawdown * 100,
                        "return_pct": f.total_return_pct,
                    }
                    for f in report.folds
                ],
            })
    except Exception as e:  # noqa: BLE001
        _compare_cache["error"] = f"Walk-forward failed: {type(e).__name__}: {e}"
        return RedirectResponse("/compare", status_code=303)

    _compare_cache["wf_reports"] = reports
    _compare_cache["error"] = None
    return RedirectResponse("/compare", status_code=303)


@app.post("/compare")
def compare_run(symbols: str = Form(""), days: int = Form(30), timeframe: str = Form("1h"), strategy: str = Form("")):
    from backtest.compare import run_comparison, strategy_by_label  # noqa: PLC0415

    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    _compare_cache["error"] = None
    if not sym_list:
        _compare_cache["error"] = "Need at least one symbol."
        return RedirectResponse("/compare", status_code=303)
    try:
        entry = strategy_by_label(strategy)
        results = run_comparison(
            sym_list, days=int(days), timeframe=timeframe, strategy_fn=entry.fn
        )
    except Exception as e:  # noqa: BLE001
        _compare_cache["error"] = f"{type(e).__name__}: {e}"
        _compare_cache["results"] = None
        return RedirectResponse("/compare", status_code=303)
    _compare_cache["results"] = results
    _compare_cache["params"] = {
        "symbols": symbols, "days": days, "timeframe": timeframe, "strategy": strategy,
    }
    _compare_cache["wf_reports"] = None  # invalidate prior validation
    return RedirectResponse("/compare", status_code=303)


_TAPE_LIMIT = 5000


def _row_to_tape_dict(r: dict) -> dict:
    """Flatten a decision-log row into one TAPE table row."""
    import json as _json  # noqa: PLC0415

    ts = int(r.get("timestamp_ms", 0))
    utc = pd.to_datetime(ts, unit="ms", utc=True) if ts else None
    meta = r.get("metadata_json")
    mode = ""
    if meta:
        try:
            o = _json.loads(meta) if isinstance(meta, str) else {}
            if isinstance(o, dict):
                mode = str(o.get("mode", "") or "")
        except (TypeError, ValueError):
            mode = ""
    rat = r.get("rationale") or ""
    if len(rat) > 120:
        rat = rat[:117] + "…"
    return {
        "id": r.get("id"),
        "ts": utc.strftime("%Y-%m-%d %H:%M:%S") if utc is not None else "",
        "event": r.get("event_type", ""),
        "symbol": r.get("symbol") or "",
        "side": r.get("side") or "",
        "strategy": r.get("strategy_id") or "",
        "price": r.get("price"),
        "qty": r.get("quantity"),
        "pnl": r.get("pnl"),
        "coid": (r.get("client_order_id") or "")[:16],
        "mode": mode,
        "rationale": rat,
    }


def _collapse_repeats(rows: list[dict]) -> list[dict]:
    """Collapse runs of consecutive identical (event_type, rationale[:60]) rows into one
    summary row with a `repeat_count`. Tracks the id and timestamp range of the group
    so the UI can show "newest → oldest".

    Trading bots can hammer the log when something is broken (network outage, missing
    cert, bad credentials) — TAPE becomes unreadable when 99% of rows are identical
    error messages. This collapse is cosmetic only; the underlying SQLite log is intact.
    """
    out: list[dict] = []
    for r in rows:
        key = (r.get("event"), (r.get("rationale") or "")[:60])
        if out and (out[-1].get("event"), (out[-1].get("rationale") or "")[:60]) == key:
            grp = out[-1]
            grp["repeat_count"] = grp.get("repeat_count", 1) + 1
            grp["last_id"] = r["id"]
            grp["last_ts"] = r["ts"]
        else:
            out.append({
                **r,
                "repeat_count": 1,
                "first_id": r["id"], "last_id": r["id"],
                "first_ts": r["ts"], "last_ts": r["ts"],
            })
    return out


@app.get("/tape.csv")
def tape_csv(event: list[str] | None = None):
    """Download the (filtered) decision log as CSV — opens cleanly in Excel/Numbers."""
    log_path, _, _ = _runtime_paths()
    rows = _read_rows(log_path)
    if event:
        sel = set(event)
        rows = [r for r in rows if str(r.get("event_type", "")) in sel]
    rows = rows[-_TAPE_LIMIT:]

    def gen():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "id", "ts_utc", "event_type", "symbol", "side", "strategy_id",
            "price", "quantity", "pnl", "client_order_id", "mode", "rationale",
        ])
        yield buf.getvalue()
        buf.seek(0); buf.truncate(0)
        for r in rows:
            d = _row_to_tape_dict(r)
            writer.writerow([
                d["id"], d["ts"], d["event"], d["symbol"], d["side"], d["strategy"],
                d["price"], d["qty"], d["pnl"], d["coid"], d["mode"], d["rationale"],
            ])
            yield buf.getvalue()
            buf.seek(0); buf.truncate(0)

    headers = {"Content-Disposition": "attachment; filename=traderbot-tape.csv"}
    return StreamingResponse(gen(), media_type="text/csv", headers=headers)


def _parse_date_to_ms(s: str | None, *, end_of_day: bool = False) -> int | None:
    """Parse YYYY-MM-DD into UTC epoch ms. Returns None on empty/bad input."""
    if not s:
        return None
    try:
        ts = pd.Timestamp(s, tz="UTC")
        if end_of_day:
            ts = ts + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
        return int(ts.timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return None


@app.get("/tape", response_class=HTMLResponse)
def tape(
    request: Request,
    event: list[str] | None = None,
    raw: int = 0,
    from_date: str | None = None,
    to_date: str | None = None,
):
    log_path, _, _ = _runtime_paths()
    rows = _read_rows(log_path)

    all_event_types = sorted({str(r.get("event_type", "")) for r in rows if r.get("event_type")})
    selected = set(event or all_event_types)

    # Date range filter (UTC). Both bounds inclusive; either may be empty.
    from_ms = _parse_date_to_ms(from_date)
    to_ms = _parse_date_to_ms(to_date, end_of_day=True)
    in_range = lambda r: (from_ms is None or int(r["timestamp_ms"]) >= from_ms) and \
                         (to_ms   is None or int(r["timestamp_ms"]) <= to_ms)
    filtered = [r for r in rows if str(r.get("event_type", "")) in selected and in_range(r)]
    tail = filtered[-_TAPE_LIMIT:]
    raw_rows = [_row_to_tape_dict(r) for r in reversed(tail)]
    tape_rows = raw_rows if raw else _collapse_repeats(raw_rows)

    return templates.TemplateResponse(
        request,
        "tape.html",
        _base_ctx(
            request, "tape",
            tape_rows=tape_rows,
            all_event_types=all_event_types,
            selected_events=sorted(selected),
            total_rows=len(rows),
            raw_count=len(raw_rows),
            shown=len(tape_rows),
            filtered_count=len(filtered),
            log_path=str(log_path),
            collapse_on=not raw,
            from_date=from_date or "",
            to_date=to_date or "",
        ),
    )


# ------------------------------------------------------------------
# SETTINGS — operator controls. POST handlers redirect (303) back to
# GET /settings so refresh-of-result-page is safe (no resubmit prompt).
# ------------------------------------------------------------------


def _settings_ctx(request: Request, *, flash: str | None = None, flash_error: str | None = None) -> dict:
    log_path, _, kill_switch_path = _runtime_paths()
    env = env_config.read_env()
    loop_status = loop_control.status()
    gate_state = get_session_state(DEFAULT_TOKEN_PATH)

    try:
        threshold = float(env.get("TRADERBOT_LLM_THRESHOLD", "0.3") or "0.3")
    except ValueError:
        threshold = 0.3
    llm_on = env.get("TRADERBOT_USE_LLM_FILTER", "").strip().lower() == "true"
    has_anthropic = bool(env.get("ANTHROPIC_API_KEY", "").strip())

    live_session = {"is_active": gate_state.is_active, "remaining_h": 0, "remaining_m": 0}
    if gate_state.is_active and gate_state.seconds_remaining is not None:
        live_session["remaining_h"] = gate_state.seconds_remaining // 3600
        live_session["remaining_m"] = (gate_state.seconds_remaining % 3600) // 60

    # Live-mode state machine: PAPER → LIVE_DRY → LIVE_REAL.
    # Flags: LIVE_TRADING (env), KRAKEN_DRY_RUN (env, default true), session token, kraken keys.
    live_flag = env.get("LIVE_TRADING", "false").strip().lower() == "true"
    dry_run = env.get("KRAKEN_DRY_RUN", "true").strip().lower() == "true"
    has_kraken = bool(env.get("KRAKEN_API_KEY", "") and env.get("KRAKEN_API_SECRET", ""))
    if not live_flag:
        live_mode = "paper"
    elif dry_run:
        live_mode = "live_dry"
    else:
        live_mode = "live_real"

    # Daily-summary cron status (Telegram digest at fixed local time)
    try:
        cron_status = cron_manage.status()
        cron_ctx = {
            "installed": cron_status.installed,
            "hour": cron_status.hour,
            "minute": cron_status.minute,
            "tg_configured": bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")),
        }
    except Exception:  # noqa: BLE001
        cron_ctx = {"installed": False, "hour": 9, "minute": 13, "tg_configured": False}

    # Per-symbol strategy mapping for the Settings overview card
    symbols_env = env.get("TRADERBOT_SYMBOLS", "").strip()
    settings_symbols = [s.strip() for s in symbols_env.split(",") if s.strip()]
    if not settings_symbols:
        settings_symbols = [env.get("TRADERBOT_SYMBOL", "BTC/USDT")]
    default_strategy_settings = (
        env.get("TRADERBOT_STRATEGY")
        or env.get("TRADERBOT_STRATEGY_ID")
        or "regime_aware_dipbuy"
    )
    per_sym_raw_settings = env.get("TRADERBOT_STRATEGY_PER_SYMBOL", "").strip()
    settings_strategy_by_symbol: dict[str, str] = {}
    if per_sym_raw_settings:
        for pair in per_sym_raw_settings.split(","):
            pair = pair.strip()
            if ":" in pair:
                k, v = (p.strip() for p in pair.split(":", 1))
                if k and v:
                    settings_strategy_by_symbol[k] = v
    for s in settings_symbols:
        settings_strategy_by_symbol.setdefault(s, default_strategy_settings)
    settings_strategy_counts = {
        "dipbuy": sum(1 for v in settings_strategy_by_symbol.values() if "dipbuy" in v),
        "union": sum(1 for v in settings_strategy_by_symbol.values() if "union" in v),
    }

    return _base_ctx(
        request,
        "settings",
        loop_running=loop_status.running,
        kill_on=kill_switch_active(kill_switch_path),
        live_session=live_session,
        env={
            "tg_token": env.get("TELEGRAM_BOT_TOKEN", ""),
            "tg_chat": env.get("TELEGRAM_CHAT_ID", ""),
            "anthropic_key": env.get("ANTHROPIC_API_KEY", ""),
            "kraken_key": env.get("KRAKEN_API_KEY", ""),
            "kraken_secret": env.get("KRAKEN_API_SECRET", ""),
        },
        llm={"on": llm_on, "has_key": has_anthropic, "threshold": threshold},
        live_mode=live_mode,
        has_kraken_keys=has_kraken,
        daily_cron=cron_ctx,
        strategy_by_symbol=settings_strategy_by_symbol,
        symbol_strategy_counts=settings_strategy_counts,
        log_path=str(log_path),
        project_name=_PROJECT_ROOT.name,
        flash=flash,
        flash_error=flash_error,
    )


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request, flash: str | None = None, flash_error: str | None = None):
    return templates.TemplateResponse(
        request, "settings.html",
        _settings_ctx(request, flash=flash, flash_error=flash_error),
    )


def _redirect(flash: str | None = None, flash_error: str | None = None) -> RedirectResponse:
    qs = []
    if flash:
        qs.append(f"flash={flash}")
    if flash_error:
        qs.append(f"flash_error={flash_error}")
    url = "/settings" + (("?" + "&".join(qs)) if qs else "")
    return RedirectResponse(url, status_code=303)


@app.post("/settings/loop/start")
def loop_start():
    s = loop_control.start()
    if s.running:
        return _redirect(flash=f"Loop started (PID {s.pid}).")
    return _redirect(flash_error="Loop failed to start — check logs.")


@app.post("/settings/loop/stop")
def loop_stop():
    loop_control.stop()
    return _redirect(flash="Loop stopped.")


@app.post("/settings/kill-switch/enable")
def kill_switch_enable():
    _, _, kill_switch_path = _runtime_paths()
    kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
    kill_switch_path.touch()
    return _redirect(flash="Kill switch ENABLED — bot is paused.")


@app.post("/settings/kill-switch/disable")
def kill_switch_disable():
    _, _, kill_switch_path = _runtime_paths()
    if kill_switch_path.exists():
        kill_switch_path.unlink()
    return _redirect(flash="Kill switch disabled — bot may trade.")


@app.post("/settings/live-session/activate")
def live_session_activate(phrase: str = Form("")):
    try:
        activate_live_session(phrase, DEFAULT_TOKEN_PATH)
        return _redirect(flash="Live session activated.")
    except ValueError as e:
        return _redirect(flash_error=f"Activation failed: {e}")


@app.post("/settings/live-session/deactivate")
def live_session_deactivate():
    deactivate_live_session(DEFAULT_TOKEN_PATH)
    return _redirect(flash="Live session deactivated.")


@app.post("/settings/telegram/save")
def telegram_save(token: str = Form(""), chat_id: str = Form("")):
    if not (token and chat_id):
        return _redirect(flash_error="Both token and chat ID required.")
    env_config.update_env({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id})
    return _redirect(flash="Telegram saved. Restart loop to apply.")


@app.post("/settings/telegram/test")
def telegram_test(token: str = Form(""), chat_id: str = Form("")):
    if not (token and chat_id):
        return _redirect(flash_error="Both token and chat ID required.")
    try:
        TelegramNotifier(token=token, chat_id=chat_id).notify(
            "INFO", "Test from traderbot", "Alerts are wired correctly."
        )
        return _redirect(flash="Test sent.")
    except Exception as e:  # noqa: BLE001
        return _redirect(flash_error=f"{type(e).__name__}: {e}")


@app.post("/settings/kraken/save")
def kraken_save(key: str = Form(""), secret: str = Form("")):
    if not (key and secret):
        return _redirect(flash_error="Both key and secret required.")
    env_config.update_env({"KRAKEN_API_KEY": key, "KRAKEN_API_SECRET": secret})
    return _redirect(flash="Kraken keys saved. Restart loop to apply.")


@app.post("/settings/llm/save")
def llm_save(api_key: str = Form(""), threshold: str = Form("0.3")):
    if not api_key:
        return _redirect(flash_error="Anthropic API key required to enable.")
    try:
        thr = float(threshold)
    except ValueError:
        return _redirect(flash_error="Invalid threshold value.")
    updates = {
        "TRADERBOT_USE_LLM_FILTER": "true",
        "TRADERBOT_LLM_THRESHOLD": str(thr),
        "ANTHROPIC_API_KEY": api_key,
    }
    env_config.update_env(updates)
    return _redirect(flash="LLM filter saved. Restart loop to apply.")


@app.post("/settings/llm/disable")
def llm_disable():
    env_config.update_env({"TRADERBOT_USE_LLM_FILTER": ""})
    return _redirect(flash="LLM filter disabled. Restart loop to apply.")


@app.post("/settings/live-mode/enable-dry")
def live_mode_enable_dry():
    """Step PAPER → LIVE_DRY: connects to real Kraken but doesn't place orders."""
    env_config.update_env({"LIVE_TRADING": "true", "KRAKEN_DRY_RUN": "true"})
    return _redirect(flash="Live mode: DRY-RUN. Restart loop to apply. Kraken auth tested, no real orders.")


@app.post("/settings/live-mode/go-real")
def live_mode_go_real(confirm: str = Form("")):
    """Step LIVE_DRY → LIVE_REAL: actual Kraken orders. Requires explicit confirm."""
    if confirm != "yes":
        return _redirect(flash_error="Confirmation required to enable real orders.")
    env_config.update_env({"LIVE_TRADING": "true", "KRAKEN_DRY_RUN": "false"})
    return _redirect(flash="LIVE mode: REAL orders enabled. Restart loop. Activate live session next.")


@app.post("/settings/live-mode/back-to-dry")
def live_mode_back_to_dry():
    env_config.update_env({"LIVE_TRADING": "true", "KRAKEN_DRY_RUN": "true"})
    return _redirect(flash="Reverted to DRY-RUN. Restart loop. No real orders will be placed.")


@app.post("/settings/live-mode/disable")
def live_mode_disable():
    """Back to PAPER mode — fully offline from Kraken."""
    env_config.update_env({"LIVE_TRADING": "false"})
    return _redirect(flash="Live mode: OFF (PAPER). Restart loop.")


@app.post("/settings/daily-summary/install")
def daily_summary_install():
    """Install the standard 6×/day schedule (06/09/12/15/18/21).
    The single-hour form has been retired — the operator gets the full
    schedule on install. Custom timing requires .env or `crontab -e` directly."""
    try:
        cron_manage.install_multi([6, 9, 12, 15, 18, 21], minute=0)
    except (ValueError, RuntimeError) as e:
        return _redirect(flash_error=f"Schedule failed: {e}")
    return _redirect(flash="Daily summary scheduled (06/09/12/15/18/21).")


@app.post("/settings/daily-summary/uninstall")
def daily_summary_uninstall():
    try:
        cron_manage.uninstall()
    except RuntimeError as e:
        return _redirect(flash_error=f"Could not remove schedule: {e}")
    return _redirect(flash="Daily summary schedule removed.")


@app.post("/settings/daily-summary/test")
def daily_summary_test():
    """Run the summary script once now and post to Telegram."""
    import subprocess  # noqa: PLC0415

    res = subprocess.run(
        ["uv", "run", "python", "-m", "tools.daily_summary"],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if res.returncode != 0:
        return _redirect(flash_error=f"Summary failed: {res.stderr.strip()[:200]}")
    return _redirect(flash="Summary sent to Telegram.")


@app.post("/settings/log/reset")
def log_reset(confirm: str = Form("")):
    if confirm != "yes":
        return _redirect(flash_error="Confirmation required.")
    log_path, _, _ = _runtime_paths()
    if loop_control.status().running:
        loop_control.stop()
    backup = loop_control.reset_decision_log(log_path)
    if backup:
        return _redirect(flash=f"Log reset. Backup: {backup}")
    return _redirect(flash="Nothing to reset — log was already empty.")


def main():
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
