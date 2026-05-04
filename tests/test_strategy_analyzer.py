"""Tests for tools.strategy_analyzer — per-strategy P&L + decay detection."""

from __future__ import annotations

import pandas as pd
import pytest

from tools.strategy_analyzer import (
    StrategyStats,
    decay_flags,
    parse_per_symbol_map,
    per_strategy_pnl,
)

HOUR_MS = 3_600_000


def _trade(
    symbol: str, pnl: float, exit_ts_ms: int, *, return_pct: float = 0.0
) -> dict:
    return {
        "symbol": symbol,
        "entry_ts": exit_ts_ms - HOUR_MS,
        "exit_ts": exit_ts_ms,
        "holding_ms": HOUR_MS,
        "qty": 1.0,
        "entry_price": 100.0,
        "exit_price": 100.0 + pnl,
        "pnl": pnl,
        "gross_pnl": pnl,
        "fees": 0.0,
        "return_pct": return_pct,
        "exit_reason": "signal_exit",
        "stop": None,
        "r_multiple": None,
        "direction": "long",
    }


# --- expected ---


def test_per_strategy_pnl_groups_by_symbol_mapping() -> None:
    """A per-symbol map sends each symbol's trades to the right strategy."""
    now = 1_000 * HOUR_MS
    trades = pd.DataFrame([
        _trade("BTC/USDT", +5.0, now - HOUR_MS),
        _trade("BTC/USDT", -2.0, now - 2 * HOUR_MS),
        _trade("SOL/USDT", +3.0, now - HOUR_MS),
    ])
    per_sym = {"BTC/USDT": "union", "SOL/USDT": "ensemble"}

    stats = per_strategy_pnl(
        trades, per_symbol_map=per_sym, default_strategy="dipbuy", now_ms=now
    )

    by_id = {s.strategy_id: s for s in stats}
    assert by_id["union"].trades == 2
    assert by_id["union"].total_pnl == 3.0  # +5 -2
    assert by_id["ensemble"].trades == 1
    assert by_id["ensemble"].total_pnl == 3.0


def test_per_strategy_pnl_falls_back_to_default() -> None:
    """Symbols not in the per-symbol map use the default strategy."""
    now = 1_000 * HOUR_MS
    trades = pd.DataFrame([_trade("ETH/USDT", +10.0, now - HOUR_MS)])
    stats = per_strategy_pnl(
        trades, per_symbol_map={}, default_strategy="dipbuy", now_ms=now
    )
    assert len(stats) == 1
    assert stats[0].strategy_id == "dipbuy"
    assert stats[0].total_pnl == 10.0


# --- edge ---


def test_per_strategy_pnl_empty_trades_returns_empty() -> None:
    stats = per_strategy_pnl(
        pd.DataFrame(), per_symbol_map={}, default_strategy="dipbuy", now_ms=0
    )
    assert stats == []


def test_24h_and_7d_windows_filter_correctly() -> None:
    """Trades older than the window are excluded from windowed P&L."""
    now = 100 * 24 * HOUR_MS  # day 100
    trades = pd.DataFrame([
        _trade("BTC/USDT", +1.0, now - 1 * HOUR_MS),       # in 24h
        _trade("BTC/USDT", +2.0, now - 30 * HOUR_MS),      # in 7d, not 24h
        _trade("BTC/USDT", +4.0, now - 10 * 24 * HOUR_MS),  # outside 7d
    ])
    stats = per_strategy_pnl(
        trades, per_symbol_map={}, default_strategy="dipbuy", now_ms=now
    )
    s = stats[0]
    assert s.total_pnl == 7.0
    assert s.pnl_24h == 1.0
    assert s.pnl_7d == 3.0  # 1 + 2


# --- decay flag ---


def test_decay_flag_fires_with_5plus_trades_negative_7d_low_pf() -> None:
    """5 trades, 4 of them losses, 7d P&L negative → decay."""
    now = 1_000 * HOUR_MS
    trades = pd.DataFrame([
        _trade("BTC/USDT", -10.0, now - 5 * HOUR_MS),
        _trade("BTC/USDT", -10.0, now - 4 * HOUR_MS),
        _trade("BTC/USDT", -10.0, now - 3 * HOUR_MS),
        _trade("BTC/USDT", +1.0, now - 2 * HOUR_MS),
        _trade("BTC/USDT", -10.0, now - 1 * HOUR_MS),
    ])
    stats = per_strategy_pnl(
        trades, per_symbol_map={}, default_strategy="dipbuy", now_ms=now
    )
    assert stats[0].is_decaying is True
    assert "7d_pnl" in (stats[0].decay_reason or "")
    assert decay_flags(stats) == ["dipbuy"]


def test_decay_does_not_fire_below_5_trades() -> None:
    """Sample-size guard — don't flag decay on n<5 even if all losing."""
    now = 1_000 * HOUR_MS
    trades = pd.DataFrame([
        _trade("BTC/USDT", -50.0, now - 1 * HOUR_MS),
        _trade("BTC/USDT", -50.0, now - 2 * HOUR_MS),
    ])
    stats = per_strategy_pnl(
        trades, per_symbol_map={}, default_strategy="dipbuy", now_ms=now
    )
    assert stats[0].is_decaying is False
    assert decay_flags(stats) == []


def test_decay_does_not_fire_when_pf_high_despite_negative_7d() -> None:
    """Big losses but big wins — PF > 0.7 → not decaying yet."""
    now = 1_000 * HOUR_MS
    trades = pd.DataFrame([
        _trade("BTC/USDT", +100.0, now - 5 * HOUR_MS),
        _trade("BTC/USDT", +100.0, now - 4 * HOUR_MS),
        _trade("BTC/USDT", +100.0, now - 3 * HOUR_MS),
        _trade("BTC/USDT", -50.0, now - 2 * HOUR_MS),
        _trade("BTC/USDT", -100.0, now - 1 * HOUR_MS),
    ])
    stats = per_strategy_pnl(
        trades, per_symbol_map={}, default_strategy="dipbuy", now_ms=now
    )
    assert stats[0].total_pnl == 150.0
    assert stats[0].is_decaying is False


# --- env parse ---


def test_parse_per_symbol_map_handles_empty() -> None:
    assert parse_per_symbol_map({"TRADERBOT_STRATEGY_PER_SYMBOL": ""}) == {}
    assert parse_per_symbol_map({}) == {}


def test_parse_per_symbol_map_handles_malformed_pairs() -> None:
    """A pair without ':' is silently skipped — operator can't break the parse."""
    raw = "BTC/USDT:union, garbage, ETH/USDT:dipbuy ,  ,SOL:ensemble"
    out = parse_per_symbol_map({"TRADERBOT_STRATEGY_PER_SYMBOL": raw})
    assert out == {"BTC/USDT": "union", "ETH/USDT": "dipbuy", "SOL": "ensemble"}
