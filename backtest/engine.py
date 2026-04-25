"""Backtest engine — long-only, single-position, signal-driven walk-forward.

Models commissions + slippage in basis points. Entries fill at the *next* bar's open
(no look-ahead). Exits trigger on intra-bar stop/target/signal — order of precedence:
stop first (conservative), then target, then signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from backtest.metrics import (
    break_even_win_rate,
    equity_returns,
    max_drawdown,
    sharpe,
    sortino,
    win_rate,
)
from risk.sizing import position_size
from signals.types import Signal


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_cash: float = 10_000.0
    risk_pct: float = 0.005
    commission_bps: float = 10.0  # 0.10% — realistic Binance spot retail
    slippage_bps: float = 5.0  # 0.05% — fills slightly worse than mid


@dataclass(frozen=True, slots=True)
class Trade:
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    return_pct: float
    exit_reason: str  # "stop" | "target" | "signal_exit" | "end_of_data"


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.Series
    metrics: dict[str, float] = field(default_factory=dict)


def _apply_costs(price: float, side: str, *, slippage_bps: float, commission_bps: float) -> float:
    """Adjusted fill price after slippage. Commission applies separately to notional."""
    slip = slippage_bps / 10_000.0
    return price * (1 + slip) if side == "buy" else price * (1 - slip)


_DEFAULT_CONFIG = BacktestConfig()


def run_backtest(
    df: pd.DataFrame,
    signals: list[Signal],
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Walk forward bar-by-bar. One open position at a time. Long-only.

    Entries fill at next bar's open (signals are decided at close of bar i, so the
    earliest possible fill is the open of bar i+1 — no peek).
    """
    if len(signals) != len(df):
        raise ValueError(f"len(signals)={len(signals)} != len(df)={len(df)}")
    config = config or _DEFAULT_CONFIG

    cash = config.initial_cash
    equity_history: list[float] = []
    trades: list[Trade] = []

    open_qty: float = 0.0
    open_entry_price: float = 0.0
    open_entry_ts: int = 0
    open_stop: float = 0.0
    open_target: float = 0.0

    bars_high = df["high"].to_numpy()
    bars_low = df["low"].to_numpy()
    bars_open = df["open"].to_numpy()
    bars_close = df["close"].to_numpy()
    bars_ts = df["timestamp_ms"].to_numpy()
    n = len(df)

    for i in range(n):
        if open_qty > 0:
            hit_stop = bars_low[i] <= open_stop
            hit_target = bars_high[i] >= open_target
            exit_reason: str | None = None
            exit_price: float = 0.0
            if hit_stop:
                exit_price = open_stop
                exit_reason = "stop"
            elif hit_target:
                exit_price = open_target
                exit_reason = "target"
            elif signals[i].side == "sell":
                if i + 1 < n:
                    exit_price = float(bars_open[i + 1])
                    exit_reason = "signal_exit"

            if exit_reason is not None:
                fill = _apply_costs(
                    exit_price,
                    "sell",
                    slippage_bps=config.slippage_bps,
                    commission_bps=config.commission_bps,
                )
                proceeds = fill * open_qty
                commission = proceeds * config.commission_bps / 10_000.0
                cash += proceeds - commission
                pnl = (fill - open_entry_price) * open_qty - commission
                trades.append(
                    Trade(
                        entry_ts=open_entry_ts,
                        exit_ts=int(bars_ts[i]),
                        entry_price=open_entry_price,
                        exit_price=fill,
                        quantity=open_qty,
                        pnl=pnl,
                        return_pct=(fill / open_entry_price - 1.0),
                        exit_reason=exit_reason,
                    )
                )
                open_qty = 0.0

        if open_qty == 0.0 and signals[i].side == "buy" and i + 1 < n:
            sig = signals[i]
            assert sig.stop is not None and sig.target is not None
            entry = float(bars_open[i + 1])
            fill = _apply_costs(
                entry,
                "buy",
                slippage_bps=config.slippage_bps,
                commission_bps=config.commission_bps,
            )
            equity_now = cash + open_qty * bars_close[i]
            qty = position_size(equity_now, fill, sig.stop, risk_pct=config.risk_pct)
            cost = qty * fill
            commission = cost * config.commission_bps / 10_000.0
            if qty > 0 and cost + commission <= cash:
                cash -= cost + commission
                open_qty = qty
                open_entry_price = fill
                open_entry_ts = int(bars_ts[i + 1])
                open_stop = sig.stop
                open_target = sig.target

        equity_history.append(cash + open_qty * bars_close[i])

    if open_qty > 0:
        last_close = float(bars_close[-1])
        fill = _apply_costs(
            last_close,
            "sell",
            slippage_bps=config.slippage_bps,
            commission_bps=config.commission_bps,
        )
        proceeds = fill * open_qty
        commission = proceeds * config.commission_bps / 10_000.0
        cash += proceeds - commission
        trades.append(
            Trade(
                entry_ts=open_entry_ts,
                exit_ts=int(bars_ts[-1]),
                entry_price=open_entry_price,
                exit_price=fill,
                quantity=open_qty,
                pnl=(fill - open_entry_price) * open_qty - commission,
                return_pct=(fill / open_entry_price - 1.0),
                exit_reason="end_of_data",
            )
        )
        equity_history[-1] = cash

    equity = pd.Series(equity_history, index=df["timestamp_ms"], name="equity")
    rets = equity_returns(equity)
    pnls = [t.pnl for t in trades]
    metrics = {
        "num_trades": float(len(trades)),
        "total_return_pct": float(equity.iloc[-1] / config.initial_cash - 1.0),
        "win_rate": win_rate(pnls),
        "break_even_wr_2to1": break_even_win_rate(2.0),
        "sharpe": sharpe(rets),
        "sortino": sortino(rets),
        "max_drawdown": max_drawdown(equity),
        "ending_cash": float(equity.iloc[-1]),
    }
    return BacktestResult(trades=trades, equity_curve=equity, metrics=metrics)
