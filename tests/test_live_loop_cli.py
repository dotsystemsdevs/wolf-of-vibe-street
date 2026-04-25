"""Tests for the env-driven CLI entry in workers.live_loop."""

from __future__ import annotations

from pathlib import Path

import pytest

from workers.live_loop import LiveLoop, build_from_env


def test_build_from_env_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With no env overrides, sensible defaults: BTC/USDT 1h, $10k, 0.5% risk, paper."""
    for k in [
        "TRADERBOT_LOG_PATH",
        "TRADERBOT_INITIAL_CASH",
        "TRADERBOT_SYMBOL",
        "TRADERBOT_TIMEFRAME",
        "TRADERBOT_POLL_INTERVAL_S",
        "TRADERBOT_RISK_PCT",
        "TRADERBOT_STRATEGY_ID",
        "TRADERBOT_HEARTBEAT_INTERVAL_S",
        "TRADERBOT_SLIPPAGE_BPS",
        "TRADERBOT_COMMISSION_BPS",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TRADERBOT_LOG_PATH", str(tmp_path / "log.db"))

    loop, cfg = build_from_env()

    assert isinstance(loop, LiveLoop)
    assert cfg["symbol"] == "BTC/USDT"
    assert cfg["timeframe"] == "1h"
    assert cfg["initial_cash"] == "$10,000.00"
    assert cfg["risk_pct"] == "0.50%"
    assert cfg["telegram"].startswith("not configured")
    assert loop.executor.cash == 10_000.0


def test_build_from_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADERBOT_LOG_PATH", str(tmp_path / "log.db"))
    monkeypatch.setenv("TRADERBOT_SYMBOL", "ETH/USDT")
    monkeypatch.setenv("TRADERBOT_INITIAL_CASH", "5000")
    monkeypatch.setenv("TRADERBOT_RISK_PCT", "0.0025")
    monkeypatch.setenv("TRADERBOT_POLL_INTERVAL_S", "60")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "cid")

    loop, cfg = build_from_env()

    assert cfg["symbol"] == "ETH/USDT"
    assert cfg["initial_cash"] == "$5,000.00"
    assert cfg["risk_pct"] == "0.25%"
    assert cfg["poll_interval_s"] == "60.0"
    assert cfg["telegram"] == "configured"
    assert loop.executor.cash == 5_000.0
