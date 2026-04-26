"""Tests for risk.live_gate — the safety interlock between paper and real money.

3-test rule (CLAUDE.md §3.1):
  - expected: env flag set correctly → enabled
  - edge: variations of falsy/truthy strings stay disabled (strict match)
  - failure: assert_* raises with a useful message when not enabled
"""

from __future__ import annotations

import pytest

from risk.live_gate import (
    CALIBRATION_TRADE_COUNT,
    LIVE_TRADING_ENV_VAR,
    assert_live_trading_enabled,
    is_live_trading_enabled,
)


def test_expected_env_true_enables_live() -> None:
    assert is_live_trading_enabled({LIVE_TRADING_ENV_VAR: "true"}) is True


def test_edge_variations_stay_disabled() -> None:
    """Strict lowercase match — anything else is paper-only. Defense against
    typos like 'True', '1', 'yes', '  true  ' silently enabling live trading."""
    assert is_live_trading_enabled({}) is False
    assert is_live_trading_enabled({LIVE_TRADING_ENV_VAR: ""}) is False
    assert is_live_trading_enabled({LIVE_TRADING_ENV_VAR: "True"}) is False
    assert is_live_trading_enabled({LIVE_TRADING_ENV_VAR: "TRUE"}) is False
    assert is_live_trading_enabled({LIVE_TRADING_ENV_VAR: "1"}) is False
    assert is_live_trading_enabled({LIVE_TRADING_ENV_VAR: "yes"}) is False
    assert is_live_trading_enabled({LIVE_TRADING_ENV_VAR: "false"}) is False
    # Whitespace is *stripped* (a common .env mistake) but the value still
    # has to be exactly "true" after stripping.
    assert is_live_trading_enabled({LIVE_TRADING_ENV_VAR: "  true  "}) is True
    assert is_live_trading_enabled({LIVE_TRADING_ENV_VAR: " True "}) is False


def test_failure_assert_raises_when_disabled() -> None:
    with pytest.raises(RuntimeError, match=LIVE_TRADING_ENV_VAR):
        assert_live_trading_enabled({})
    # Should NOT raise when enabled.
    assert_live_trading_enabled({LIVE_TRADING_ENV_VAR: "true"})


def test_calibration_count_matches_s55_rule() -> None:
    """experiences.md S-55 says first 30 live trades are calibration."""
    assert CALIBRATION_TRADE_COUNT == 30
