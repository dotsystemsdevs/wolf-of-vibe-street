"""Tests for backtest.compare — pure helpers (no network)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.compare import (
    DEFAULT_SYMBOLS,
    SymbolResult,
    _parse_env_symbols,
    rank_by_expectancy,
    render_html,
    render_table,
    run_one,
)
from backtest.engine import BacktestConfig
from data.binance import Bar
from features.compute import bars_to_df

HOUR_MS = 3_600_000


def _df(closes: list[float]) -> pd.DataFrame:
    return bars_to_df(
        [
            Bar(timestamp_ms=i * HOUR_MS, open=c, high=c + 1, low=c - 1, close=c, volume=1.0)
            for i, c in enumerate(closes)
        ]
    )


def test_parse_env_symbols_default(monkeypatch) -> None:
    monkeypatch.delenv("TRADERBOT_SYMBOLS", raising=False)
    assert _parse_env_symbols() == DEFAULT_SYMBOLS


def test_parse_env_symbols_override(monkeypatch) -> None:
    monkeypatch.setenv("TRADERBOT_SYMBOLS", "BTC/USDT, ETH/USDT")
    assert _parse_env_symbols() == ("BTC/USDT", "ETH/USDT")


def test_parse_env_symbols_empty_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("TRADERBOT_SYMBOLS", "  ")
    assert _parse_env_symbols() == DEFAULT_SYMBOLS


def test_run_one_produces_expected_fields() -> None:
    """A trending up series should give a positive buy_hold_return and a non-empty result."""
    df = _df([100.0 + i * 0.5 for i in range(50)])
    res = run_one("BTC/USDT", df, BacktestConfig(initial_cash=10_000.0))
    assert isinstance(res, SymbolResult)
    assert res.symbol == "BTC/USDT"
    assert res.bars == 50
    assert res.buy_hold_return_pct > 0
    assert res.first_close == 100.0
    assert res.last_close == 100.0 + 49 * 0.5


def test_render_table_includes_each_symbol() -> None:
    df1 = _df([100.0 + i * 0.5 for i in range(60)])
    df2 = _df([100.0 - i * 0.3 for i in range(60)])
    cfg = BacktestConfig(initial_cash=10_000.0)
    results = [run_one("BTC/USDT", df1, cfg), run_one("ETH/USDT", df2, cfg)]

    table = render_table(results)
    assert "BTC/USDT" in table
    assert "ETH/USDT" in table
    assert "Symbol" in table  # header row
    assert "Sharpe" in table


def test_rank_by_expectancy_orders_best_first() -> None:
    """A trending-up symbol must rank above a trending-down symbol."""
    up = _df([100.0 + i * 0.5 for i in range(60)])
    down = _df([100.0 - i * 0.3 for i in range(60)])
    cfg = BacktestConfig(initial_cash=10_000.0)
    results = [
        run_one("BAD/USDT", down, cfg),
        run_one("GOOD/USDT", up, cfg),
    ]
    ranked = rank_by_expectancy(results)
    # The trending-up series produces a winning trade → ranks first.
    assert ranked[0].result.metrics.get("expectancy", 0.0) >= ranked[-1].result.metrics.get(
        "expectancy", 0.0
    )


def test_rank_by_expectancy_zero_trade_symbols_sink_to_bottom() -> None:
    """Symbols where the strategy never fired must rank last regardless of price action."""
    flat = _df([100.0] * 60)  # no movement → no signals → no trades
    up = _df([100.0 + i * 0.5 for i in range(60)])
    cfg = BacktestConfig(initial_cash=10_000.0)
    results = [
        run_one("FLAT/USDT", flat, cfg),
        run_one("GOOD/USDT", up, cfg),
    ]
    ranked = rank_by_expectancy(results)
    # FLAT must be last; GOOD must be first.
    assert ranked[0].symbol == "GOOD/USDT"
    assert ranked[-1].symbol == "FLAT/USDT"


def test_render_html_writes_file(tmp_path: Path) -> None:
    df = _df([100.0 + i * 0.5 for i in range(60)])
    cfg = BacktestConfig(initial_cash=10_000.0)
    results = [run_one("BTC/USDT", df, cfg)]

    out_path = tmp_path / "report.html"
    written = render_html(results, out_path)
    assert written == out_path
    text = out_path.read_text()
    assert "<html" in text.lower()
    # Plotly may HTML-escape the / in BTC/USDT; check the encoded forms too.
    assert any(token in text for token in ("BTC/USDT", "BTC\\u002fUSDT", "BTC\\/USDT"))
    assert "plotly" in text.lower()
