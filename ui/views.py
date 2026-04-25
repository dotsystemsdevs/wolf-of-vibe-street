"""Pure summary functions over decision-log rows.

Kept dependency-free of Streamlit so the logic is testable in isolation. The dashboard
imports these and renders the dicts/DataFrames they return.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd


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
    entry_ts, exit_ts, qty, entry_price, exit_price, pnl, return_pct, exit_reason.
    """
    fills = [r for r in rows if r["event_type"] == "order_filled"]
    trades: list[dict[str, Any]] = []
    open_buy: dict[str, Any] | None = None
    for f in fills:
        if f["side"] == "buy":
            open_buy = f
        elif f["side"] == "sell" and open_buy is not None:
            qty = float(open_buy["quantity"])
            entry_px = float(open_buy["price"])
            exit_px = float(f["price"])
            pnl = (exit_px - entry_px) * qty
            trades.append(
                {
                    "entry_ts": int(open_buy["timestamp_ms"]),
                    "exit_ts": int(f["timestamp_ms"]),
                    "qty": qty,
                    "entry_price": entry_px,
                    "exit_price": exit_px,
                    "pnl": pnl,
                    "return_pct": exit_px / entry_px - 1.0,
                    "exit_reason": f["rationale"] or "",
                }
            )
            open_buy = None
    return (
        pd.DataFrame(trades)
        if trades
        else pd.DataFrame(
            columns=[
                "entry_ts",
                "exit_ts",
                "qty",
                "entry_price",
                "exit_price",
                "pnl",
                "return_pct",
                "exit_reason",
            ]
        )
    )


def equity_curve(rows: list[dict[str, Any]], initial_cash: float) -> pd.DataFrame:
    """Walk fills accumulating cash + open position; return equity at each fill timestamp.

    Long-only, single-position assumption (Phase 1). Equity = cash + open_qty * last_price.
    Includes a synthetic starting point at the first fill timestamp - 1 ms with cash=initial.
    """
    fills = sorted(
        (r for r in rows if r["event_type"] == "order_filled"),
        key=lambda r: (r["timestamp_ms"], r["id"]),
    )
    if not fills:
        return pd.DataFrame(columns=["timestamp_ms", "cash", "position_value", "equity"])

    cash = float(initial_cash)
    qty = 0.0
    avg_entry = 0.0
    last_price = 0.0
    points: list[dict[str, float]] = [
        {
            "timestamp_ms": int(fills[0]["timestamp_ms"]) - 1,
            "cash": cash,
            "position_value": 0.0,
            "equity": cash,
        }
    ]
    for f in fills:
        price = float(f["price"])
        fill_qty = float(f["quantity"])
        last_price = price
        if f["side"] == "buy":
            cost = price * fill_qty
            cash -= cost
            new_qty = qty + fill_qty
            avg_entry = (avg_entry * qty + price * fill_qty) / new_qty if new_qty > 0 else price
            qty = new_qty
        else:
            cash += price * fill_qty
            qty -= fill_qty
            if qty <= 0:
                qty = 0.0
                avg_entry = 0.0
        position_value = qty * last_price
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
    """Reconstruct currently-open positions by walking fills."""
    fills = sorted(
        (r for r in rows if r["event_type"] == "order_filled"),
        key=lambda r: (r["timestamp_ms"], r["id"]),
    )
    pos: dict[str, dict[str, float]] = {}
    for f in fills:
        sym = f["symbol"]
        price = float(f["price"])
        fill_qty = float(f["quantity"])
        cur = pos.get(sym, {"qty": 0.0, "avg_entry": 0.0, "last_price": 0.0})
        cur["last_price"] = price
        if f["side"] == "buy":
            new_qty = cur["qty"] + fill_qty
            cur["avg_entry"] = (
                (cur["avg_entry"] * cur["qty"] + price * fill_qty) / new_qty
                if new_qty > 0
                else price
            )
            cur["qty"] = new_qty
        else:
            cur["qty"] -= fill_qty
            if cur["qty"] <= 1e-9:
                cur = {"qty": 0.0, "avg_entry": 0.0, "last_price": price}
        pos[sym] = cur
    return [
        {
            "symbol": sym,
            "qty": p["qty"],
            "avg_entry": p["avg_entry"],
            "last_price": p["last_price"],
            "unrealized_pnl": (p["last_price"] - p["avg_entry"]) * p["qty"],
        }
        for sym, p in pos.items()
        if p["qty"] > 0
    ]


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
