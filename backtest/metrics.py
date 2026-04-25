"""Backtest metrics — Sharpe, Sortino, max drawdown, win rate, break-even WR check (S-50)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def sharpe(returns: pd.Series, periods_per_year: int = 8760) -> float:
    """Annualized Sharpe (assumes risk-free rate = 0). 8760 = hours/year for 1h bars."""
    r = returns.dropna()
    if len(r) < 2 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * math.sqrt(periods_per_year))


def sortino(returns: pd.Series, periods_per_year: int = 8760) -> float:
    """Annualized Sortino — downside-deviation denominator."""
    r = returns.dropna()
    if len(r) < 2:
        return 0.0
    downside = r[r < 0]
    if len(downside) == 0 or downside.std() == 0:
        return 0.0
    return float(r.mean() / downside.std() * math.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series) -> float:
    """Peak-to-trough drawdown as a positive fraction (e.g. 0.18 = 18% drawdown)."""
    if len(equity) == 0:
        return 0.0
    running_max = equity.cummax()
    dd = (equity - running_max) / running_max
    return float(-dd.min())


def win_rate(trade_pnls: list[float]) -> float:
    """Fraction of trades with pnl > 0."""
    if not trade_pnls:
        return 0.0
    wins = sum(1 for p in trade_pnls if p > 0)
    return wins / len(trade_pnls)


def break_even_win_rate(rr_ratio: float) -> float:
    """S-50: minimum WR needed to break even at this reward/risk. e.g. 2:1 → 0.333."""
    if rr_ratio <= 0:
        raise ValueError(f"rr_ratio must be > 0, got {rr_ratio}")
    return 1.0 / (1.0 + rr_ratio)


def equity_returns(equity: pd.Series) -> pd.Series:
    """Per-period log returns of an equity curve, suitable for sharpe/sortino."""
    if len(equity) < 2:
        return pd.Series(dtype=float)
    return np.log(equity / equity.shift(1)).dropna()
