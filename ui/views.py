"""Pure summary functions over decision-log rows.

Kept dependency-free of Streamlit so the logic is testable in isolation. The dashboard
imports these and renders the dicts/DataFrames they return.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pandas as pd


def _fee_from_row(row: dict[str, Any]) -> float:
    """Extract `fee` from a row's metadata_json blob; 0.0 if absent (legacy rows)."""
    raw = row.get("metadata_json")
    if not raw:
        return 0.0
    try:
        meta = json.loads(raw)
    except (TypeError, ValueError):
        return 0.0
    fee = meta.get("fee", 0.0) if isinstance(meta, dict) else 0.0
    try:
        return float(fee)
    except (TypeError, ValueError):
        return 0.0


def event_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(r["event_type"] for r in rows))


def fills_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Just the order_filled rows as a DataFrame, sorted ascending by id."""
    fills = [r for r in rows if r["event_type"] == "order_filled"]
    if not fills:
        return pd.DataFrame(
            columns=["id", "timestamp_ms", "side", "symbol", "quantity", "price", "rationale"]
        )
    df = pd.DataFrame(fills)
    return df[["id", "timestamp_ms", "side", "symbol", "quantity", "price", "rationale"]].copy()


def trades_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Pair buy fills with the next sell fill (FIFO) → realized round-trip trades.

    Single-position simplification: assume one open at a time. Returns columns:
    entry_ts, exit_ts, holding_ms, qty, entry_price, exit_price, pnl, return_pct,
    exit_reason, stop, r_multiple. `stop` + `r_multiple` are populated when the
    corresponding order_placed event carries stop in its metadata.
    """
    import json as _json  # noqa: PLC0415

    # signal_id → stop level lookup (from order_placed metadata, BUY only)
    stops_by_sig: dict[tuple[str, str | None], float | None] = {}
    for r in rows:
        if r["event_type"] != "order_placed" or r.get("side") != "buy":
            continue
        meta = r.get("metadata_json")
        if not meta:
            continue
        try:
            d = _json.loads(meta) if isinstance(meta, str) else meta
        except (TypeError, ValueError):
            continue
        stops_by_sig[(r["symbol"], r.get("signal_id"))] = d.get("stop")

    fills = [r for r in rows if r["event_type"] == "order_filled"]
    trades: list[dict[str, Any]] = []
    # Per-symbol open-buy tracker. Without this, a SOL sell would pair with a
    # BTC buy if BTC was the most recently opened position — the math then
    # crosses asset prices (e.g. exit SOL at $80 vs entry BTC at $76,200) and
    # produces a phantom $260k+ "trade". Real bug found 2026-05-02.
    open_buy_by_sym: dict[str, dict[str, Any]] = {}
    for f in fills:
        sym = str(f.get("symbol") or "")
        if f["side"] == "buy":
            open_buy_by_sym[sym] = f
        elif f["side"] == "sell" and sym in open_buy_by_sym:
            open_buy = open_buy_by_sym.pop(sym)
            qty = float(open_buy["quantity"])
            entry_px = float(open_buy["price"])
            exit_px = float(f["price"])
            entry_fee = _fee_from_row(open_buy)
            exit_fee = _fee_from_row(f)
            gross = (exit_px - entry_px) * qty
            pnl = gross - entry_fee - exit_fee
            stop_px = stops_by_sig.get((sym, open_buy.get("signal_id")))
            # R-multiple: pnl / initial_risk. Long-only so risk = (entry - stop) * qty.
            # Skip if stop is missing or above entry (degenerate).
            r_multiple: float | None = None
            if stop_px is not None and entry_px > stop_px:
                initial_risk = (entry_px - stop_px) * qty
                if initial_risk > 0:
                    r_multiple = pnl / initial_risk
            trades.append(
                {
                    "symbol": sym,
                    "entry_ts": int(open_buy["timestamp_ms"]),
                    "exit_ts": int(f["timestamp_ms"]),
                    "holding_ms": int(f["timestamp_ms"]) - int(open_buy["timestamp_ms"]),
                    "qty": qty,
                    "entry_price": entry_px,
                    "exit_price": exit_px,
                    "pnl": pnl,
                    "gross_pnl": gross,
                    "fees": entry_fee + exit_fee,
                    "return_pct": exit_px / entry_px - 1.0,
                    "exit_reason": f["rationale"] or "",
                    "stop": stop_px,
                    "r_multiple": r_multiple,
                }
            )
    return (
        pd.DataFrame(trades)
        if trades
        else pd.DataFrame(
            columns=[
                "symbol",
                "entry_ts",
                "exit_ts",
                "holding_ms",
                "qty",
                "entry_price",
                "exit_price",
                "pnl",
                "gross_pnl",
                "fees",
                "return_pct",
                "exit_reason",
                "stop",
                "r_multiple",
            ]
        )
    )


def equity_curve(rows: list[dict[str, Any]], initial_cash: float) -> pd.DataFrame:
    """Walk fills accumulating cash + per-symbol positions; return equity at each fill ts.

    Long-only, multi-symbol. Equity = cash + sum(per-symbol qty × that symbol's last
    known price). Each symbol tracks its own qty + last_price independently — without
    this, summing qty across symbols (BTC + LINK + SOL) and multiplying by the
    last-traded symbol's price produces a meaningless number that drifts further from
    reality with each new symbol added (real bug, found 2026-04-30).
    Includes a synthetic starting point at the first fill timestamp - 1 ms with cash=initial.
    """
    fills = sorted(
        (r for r in rows if r["event_type"] == "order_filled"),
        key=lambda r: (r["timestamp_ms"], r["id"]),
    )
    if not fills:
        return pd.DataFrame(columns=["timestamp_ms", "cash", "position_value", "equity"])

    cash = float(initial_cash)
    # Per-symbol state — qty + last_price seen for that symbol. Position value at any
    # tick = sum(qty * last_price) across all currently-held symbols.
    qty_by_sym: dict[str, float] = {}
    last_px_by_sym: dict[str, float] = {}
    points: list[dict[str, float]] = [
        {
            "timestamp_ms": int(fills[0]["timestamp_ms"]) - 1,
            "cash": cash,
            "position_value": 0.0,
            "equity": cash,
        }
    ]
    for f in fills:
        sym = str(f.get("symbol") or "")
        price = float(f["price"])
        fill_qty = float(f["quantity"])
        fee = _fee_from_row(f)
        last_px_by_sym[sym] = price
        if f["side"] == "buy":
            cash -= price * fill_qty + fee
            qty_by_sym[sym] = qty_by_sym.get(sym, 0.0) + fill_qty
        else:
            cash += price * fill_qty - fee
            qty_by_sym[sym] = qty_by_sym.get(sym, 0.0) - fill_qty
            if qty_by_sym[sym] <= 1e-9:
                qty_by_sym[sym] = 0.0
        position_value = sum(
            q * last_px_by_sym.get(s, 0.0) for s, q in qty_by_sym.items() if q > 0
        )
        points.append(
            {
                "timestamp_ms": int(f["timestamp_ms"]),
                "cash": cash,
                "position_value": position_value,
                "equity": cash + position_value,
            }
        )
    return pd.DataFrame(points)


def open_positions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct currently-open positions by walking fills.

    Enriches each open position with the most recent BUY's stop/target levels
    if they were stored in the order_placed metadata (added 2026-04-28). Older
    rows missing the metadata return None for stop/target — UI shows a dash.
    """
    fills = sorted(
        (r for r in rows if r["event_type"] == "order_filled"),
        key=lambda r: (r["timestamp_ms"], r["id"]),
    )
    pos: dict[str, dict[str, Any]] = {}
    for f in fills:
        sym = f["symbol"]
        price = float(f["price"])
        fill_qty = float(f["quantity"])
        cur = pos.get(
            sym,
            {"qty": 0.0, "avg_entry": 0.0, "last_price": 0.0, "entry_ts": 0, "signal_id": None},
        )
        cur["last_price"] = price
        if f["side"] == "buy":
            new_qty = cur["qty"] + fill_qty
            cur["avg_entry"] = (
                (cur["avg_entry"] * cur["qty"] + price * fill_qty) / new_qty
                if new_qty > 0
                else price
            )
            cur["qty"] = new_qty
            cur["entry_ts"] = int(f["timestamp_ms"])
            cur["signal_id"] = f.get("signal_id")
        else:
            cur["qty"] -= fill_qty
            if cur["qty"] <= 1e-9:
                cur = {
                    "qty": 0.0,
                    "avg_entry": 0.0,
                    "last_price": price,
                    "entry_ts": 0,
                    "signal_id": None,
                }
        pos[sym] = cur

    # Build a (symbol, signal_id) → (stop, target) map from order_placed metadata.
    stops_by_sig: dict[tuple[str, str | None], dict[str, float | None]] = {}
    for r in rows:
        if r["event_type"] != "order_placed" or r.get("side") != "buy":
            continue
        meta = r.get("metadata_json")
        if meta:
            import json as _json  # noqa: PLC0415

            try:
                d = _json.loads(meta) if isinstance(meta, str) else meta
            except (TypeError, ValueError):
                d = {}
            stops_by_sig[(r["symbol"], r.get("signal_id"))] = {
                "stop": d.get("stop"),
                "target": d.get("target"),
            }

    out: list[dict[str, Any]] = []
    for sym, p in pos.items():
        if p["qty"] <= 0:
            continue
        levels = stops_by_sig.get((sym, p.get("signal_id")), {})
        out.append(
            {
                "symbol": sym,
                "qty": p["qty"],
                "avg_entry": p["avg_entry"],
                "last_price": p["last_price"],
                "unrealized_pnl": (p["last_price"] - p["avg_entry"]) * p["qty"],
                "stop": levels.get("stop"),
                "target": levels.get("target"),
                "entry_ts": p["entry_ts"],
                "signal_id": p.get("signal_id"),
            }
        )
    return out


def day_pnl(rows: list[dict[str, Any]], *, now_ms: int) -> float:
    """Realized P&L for closed trades whose exit_ts falls within the current UTC day."""
    trades = trades_dataframe(rows)
    if trades.empty:
        return 0.0
    day_start_ms = (now_ms // 86_400_000) * 86_400_000
    today = trades[trades["exit_ts"] >= day_start_ms]
    if today.empty:
        return 0.0
    return float(today["pnl"].sum())


def soak_health(
    rows: list[dict[str, Any]],
    *,
    bot_running: bool,
    kill_switch_on: bool,
    now_ms: int,
    expected_bar_seconds: int = 3600,
    error_window_seconds: int = 3600,
    loop_started_at_ms: int | None = None,
) -> list[dict[str, str]]:
    """Soak-readiness checks. Returns a list of {name, status, message} dicts.

    `status` is one of "ok" | "warn" | "error". Designed for a morning glance after a
    multi-hour overnight run — green = bot did its job, yellow = needs attention,
    red = something broke.

    Checks:
      1. bot_process     — `loop_control.status().running` is True
      2. kill_switch     — kill switch is OFF (warn if ON, since that pauses trading)
      3. signals_fresh   — last `signal` row within 2× expected_bar_seconds
      4. tick_errors     — count of `order_rejected(tick_error: ...)` in last hour
      5. log_has_data    — at least 5 rows in the decision log
    """
    out: list[dict[str, str]] = []

    out.append(
        {
            "name": "Bot process",
            "status": "ok" if bot_running else "error",
            "message": "Live loop is running"
            if bot_running
            else "Live loop is NOT running — start it from the sidebar",
        }
    )

    out.append(
        {
            "name": "Kill switch",
            "status": "warn" if kill_switch_on else "ok",
            "message": "Kill switch is ACTIVE — bot will not enter new trades"
            if kill_switch_on
            else "Kill switch is OFF — bot may trade",
        }
    )

    signal_rows = [r for r in rows if r["event_type"] == "signal"]
    if not signal_rows:
        out.append(
            {
                "name": "Recent signals",
                "status": "warn" if bot_running else "ok",
                "message": "No signals yet — give the loop one full bar interval to produce one",
            }
        )
    else:
        last_sig_ms = max(int(r["timestamp_ms"]) for r in signal_rows)
        age_s = (now_ms - last_sig_ms) / 1000
        threshold_s = expected_bar_seconds * 2 + 600  # 2 bars + 10 min slack
        # Compute next-bar context so "68 min ago" reads as "normal for 1h" not "stuck".
        next_bar_in_s = expected_bar_seconds - ((now_ms // 1000) % expected_bar_seconds)
        nb_min = next_bar_in_s // 60
        tf_label = (
            "1h"
            if expected_bar_seconds == 3600
            else f"{expected_bar_seconds // 60}m"
            if expected_bar_seconds < 3600
            else f"{expected_bar_seconds // 3600}h"
        )
        if age_s <= threshold_s:
            out.append(
                {
                    "name": "Recent signals",
                    "status": "ok",
                    "message": (
                        f"Last signal {int(age_s / 60)} min ago "
                        f"({len(signal_rows)} total) · {tf_label} bars · "
                        f"next in {nb_min} min"
                    ),
                }
            )
        else:
            out.append(
                {
                    "name": "Recent signals",
                    "status": "error",
                    "message": (
                        f"Last signal was {int(age_s / 60)} min ago — loop may be "
                        f"stuck ({tf_label} bars; expected within "
                        f"{threshold_s // 60} min)"
                    ),
                }
            )

    # Tick errors — only count those AFTER the latest loop start so a restart
    # clears the indicator instantly. Falls back to the rolling window if we
    # don't know when the loop started.
    error_cutoff_ms = now_ms - error_window_seconds * 1000
    if loop_started_at_ms is not None:
        error_cutoff_ms = max(error_cutoff_ms, int(loop_started_at_ms))
    recent_errors = [
        r
        for r in rows
        if r["event_type"] == "order_rejected"
        and (r["rationale"] or "").startswith("tick_error")
        and int(r["timestamp_ms"]) >= error_cutoff_ms
    ]
    if not recent_errors:
        msg = "No tick errors since last loop start" if loop_started_at_ms else \
              f"No tick errors in last {error_window_seconds // 60} min"
        out.append({"name": "Tick errors", "status": "ok", "message": msg})
    else:
        n = len(recent_errors)
        sample = recent_errors[-1]["rationale"] or ""
        out.append(
            {
                "name": "Tick errors",
                "status": "warn" if n <= 2 else "error",
                "message": f"{n} tick error(s) since last restart — last: {sample[:60]}",
            }
        )

    # Position reconcile — broker vs decision-log position mismatch. Surfaced as
    # an error because it blocks new trades until resolved.
    reconciles = [r for r in rows if r["event_type"] == "reconcile"]
    if reconciles:
        last_recon = reconciles[-1]
        rat = last_recon.get("rationale") or ""
        if "FAILED" in rat or "mismatch" in rat:
            out.append({
                "name": "Position reconcile",
                "status": "error",
                "message": f"{rat[:90]} — reset log in Settings, or close the stale position manually.",
            })
        else:
            out.append({
                "name": "Position reconcile",
                "status": "ok",
                "message": "Broker positions match the decision log.",
            })

    out.append(
        {
            "name": "Decision log",
            "status": "ok" if len(rows) >= 5 else "warn",
            "message": f"{len(rows):,} rows logged",
        }
    )

    return out


def summary(rows: list[dict[str, Any]], initial_cash: float) -> dict[str, Any]:
    """High-level snapshot: trade count, win rate, realized P&L, current cash."""
    trades = trades_dataframe(rows)
    counts = event_counts(rows)
    realized_pnl = float(trades["pnl"].sum()) if not trades.empty else 0.0
    wins = int((trades["pnl"] > 0).sum()) if not trades.empty else 0
    losses = int((trades["pnl"] < 0).sum()) if not trades.empty else 0
    return {
        "rows_total": len(rows),
        "events": counts,
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / max(len(trades), 1),
        "realized_pnl": realized_pnl,
        "ending_cash_estimate": initial_cash + realized_pnl,
        "ending_return_pct": realized_pnl / initial_cash if initial_cash > 0 else 0.0,
        "blocks_by_reason": dict(
            Counter(r["rationale"] for r in rows if r["event_type"] == "risk_block")
        ),
    }
