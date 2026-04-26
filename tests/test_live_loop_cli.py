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
        "TRADERBOT_STRATEGY",
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
    # Default strategy is baseline EMA-cross.
    assert "baseline_ema_cross" in cfg["strategy"]
    assert "Baseline EMA-cross" in cfg["strategy"]


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


def test_build_from_env_selects_mean_reversion_strategy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """TRADERBOT_STRATEGY=mean_reversion_rsi must wire that strategy into the loop."""
    monkeypatch.setenv("TRADERBOT_LOG_PATH", str(tmp_path / "log.db"))
    monkeypatch.setenv("TRADERBOT_STRATEGY", "mean_reversion_rsi")
    monkeypatch.delenv("TRADERBOT_STRATEGY_ID", raising=False)

    loop, cfg = build_from_env()

    from strategies.mean_reversion_rsi import generate_signals as mean_rev_fn

    assert loop.strategy_fn is mean_rev_fn
    assert "mean_reversion_rsi" in cfg["strategy"]
    assert "Mean-reversion RSI" in cfg["strategy"]
    # The decision-log strategy_id matches what runs.
    assert loop.executor.strategy_id == "mean_reversion_rsi"


def test_build_from_env_unknown_strategy_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A typo in TRADERBOT_STRATEGY must fail loudly, not silently fall back."""
    monkeypatch.setenv("TRADERBOT_LOG_PATH", str(tmp_path / "log.db"))
    monkeypatch.setenv("TRADERBOT_STRATEGY", "doesnt_exist")

    with pytest.raises(ValueError, match="unknown TRADERBOT_STRATEGY"):
        build_from_env()


def test_build_from_env_legacy_strategy_id_var_still_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Old .env files using TRADERBOT_STRATEGY_ID continue to work as before."""
    monkeypatch.setenv("TRADERBOT_LOG_PATH", str(tmp_path / "log.db"))
    monkeypatch.delenv("TRADERBOT_STRATEGY", raising=False)
    monkeypatch.setenv("TRADERBOT_STRATEGY_ID", "mean_reversion_rsi")

    loop, cfg = build_from_env()
    assert loop.executor.strategy_id == "mean_reversion_rsi"
    assert "mean_reversion_rsi" in cfg["strategy"]


def test_build_from_env_default_broker_is_paper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without TRADERBOT_BROKER, must default to PaperBroker + mode=paper."""
    from execution.ccxt_paper import PaperBroker

    monkeypatch.setenv("TRADERBOT_LOG_PATH", str(tmp_path / "log.db"))
    monkeypatch.delenv("TRADERBOT_BROKER", raising=False)
    monkeypatch.delenv("LIVE_TRADING", raising=False)

    loop, cfg = build_from_env()
    assert isinstance(loop.executor.broker, PaperBroker)
    assert loop.executor.trade_mode == "paper"
    assert "paper" in cfg["broker"]


def test_build_from_env_kraken_broker_requires_live_trading_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """TRADERBOT_BROKER=kraken without LIVE_TRADING=true must refuse."""
    monkeypatch.setenv("TRADERBOT_LOG_PATH", str(tmp_path / "log.db"))
    monkeypatch.setenv("TRADERBOT_BROKER", "kraken")
    monkeypatch.delenv("LIVE_TRADING", raising=False)

    with pytest.raises(RuntimeError, match="LIVE_TRADING"):
        build_from_env()


def test_build_from_env_kraken_defaults_to_live_calibration_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Kraken (dry-run) + LIVE_TRADING — default trade_mode is live_calibration."""
    monkeypatch.setenv("TRADERBOT_LOG_PATH", str(tmp_path / "log.db"))
    monkeypatch.setenv("TRADERBOT_BROKER", "kraken")
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("KRAKEN_DRY_RUN", "true")
    monkeypatch.setenv("KRAKEN_API_KEY", "k")
    monkeypatch.setenv("KRAKEN_API_SECRET", "s")
    monkeypatch.delenv("TRADERBOT_TRADE_MODE", raising=False)

    loop, cfg = build_from_env()
    assert loop.executor.trade_mode == "live_calibration"
    assert "live_calibration" in cfg["broker"]


def test_build_from_env_kraken_trade_mode_live_promotion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """TRADERBOT_TRADE_MODE=live must flip executor to full-live mode (wider caps)."""
    from risk.caps import live_calibration_caps, live_full_caps  # noqa: PLC0415

    monkeypatch.setenv("TRADERBOT_LOG_PATH", str(tmp_path / "log.db"))
    monkeypatch.setenv("TRADERBOT_BROKER", "kraken")
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("KRAKEN_DRY_RUN", "true")
    monkeypatch.setenv("KRAKEN_API_KEY", "k")
    monkeypatch.setenv("KRAKEN_API_SECRET", "s")
    monkeypatch.setenv("TRADERBOT_TRADE_MODE", "live")

    loop, cfg = build_from_env()
    assert loop.executor.trade_mode == "live"
    assert "mode=live" in cfg["broker"]
    # Full-live caps: higher max notional / concurrent positions than calibration.
    cal = live_calibration_caps(initial_cash_usd=loop.executor.cash)
    full = live_full_caps(initial_cash_usd=loop.executor.cash)
    assert loop.executor.caps.max_concurrent_positions == full.max_concurrent_positions
    assert cal.max_concurrent_positions < full.max_concurrent_positions


def test_build_from_env_unknown_broker_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRADERBOT_LOG_PATH", str(tmp_path / "log.db"))
    monkeypatch.setenv("TRADERBOT_BROKER", "binance_pro_max")

    with pytest.raises(ValueError, match="unknown TRADERBOT_BROKER"):
        build_from_env()


def test_build_from_env_llm_filter_off_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without TRADERBOT_USE_LLM_FILTER, the strategy runs raw."""
    monkeypatch.setenv("TRADERBOT_LOG_PATH", str(tmp_path / "log.db"))
    monkeypatch.delenv("TRADERBOT_USE_LLM_FILTER", raising=False)

    _loop, cfg = build_from_env()
    assert cfg["llm_filter"] == "off"


def test_build_from_env_llm_filter_requires_anthropic_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Asking for the LLM filter without an API key must fail loudly."""
    monkeypatch.setenv("TRADERBOT_LOG_PATH", str(tmp_path / "log.db"))
    monkeypatch.setenv("TRADERBOT_USE_LLM_FILTER", "true")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_from_env()


def test_build_from_env_llm_filter_on_with_key_wraps_strategy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With both flag + key set, strategy_fn is the wrapped version (different __name__)."""
    monkeypatch.setenv("TRADERBOT_LOG_PATH", str(tmp_path / "log.db"))
    monkeypatch.setenv("TRADERBOT_USE_LLM_FILTER", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake-key")
    monkeypatch.setenv("TRADERBOT_LLM_THRESHOLD", "0.5")

    loop, cfg = build_from_env()
    assert cfg["llm_filter"] == "on (threshold=+0.50)"
    # Wrapper name carries the prefix.
    assert "llm_filtered" in loop.strategy_fn.__name__
