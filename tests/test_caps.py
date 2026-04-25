"""Tests for risk.caps — each cap independently + kill switch precedence."""

from __future__ import annotations

from pathlib import Path

import pytest

from risk.caps import RiskCaps, RiskState, check_entry, kill_switch_active

CLEAN = RiskState(
    equity_now=10_000.0,
    daily_high_water=10_000.0,
    weekly_high_water=10_000.0,
    open_positions_count=0,
    open_total_notional_usd=0.0,
)


def _caps(tmp_path: Path, **kw) -> RiskCaps:
    return RiskCaps(kill_switch_path=tmp_path / "KILL", **kw)


# --- happy ---


def test_expected_clean_state_allows(tmp_path: Path) -> None:
    d = check_entry(CLEAN, 1000.0, _caps(tmp_path))
    assert d.allow is True
    assert d.reason == "ok"


# --- kill switch ---


def test_kill_switch_file_blocks(tmp_path: Path) -> None:
    caps = _caps(tmp_path)
    caps.kill_switch_path.touch()
    d = check_entry(CLEAN, 1000.0, caps)
    assert d.allow is False
    assert d.reason == "kill_switch"


def test_kill_switch_env_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KILL_SWITCH", "true")
    d = check_entry(CLEAN, 1000.0, _caps(tmp_path))
    assert d.allow is False
    assert d.reason == "kill_switch"


def test_kill_switch_env_case_insensitive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KILL_SWITCH", "TRUE")
    assert kill_switch_active(tmp_path / "KILL") is True


def test_kill_switch_env_other_values_dont_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Defensive: anything other than 'true' is treated as off."""
    for v in ("false", "0", "1", "yes", ""):
        monkeypatch.setenv("KILL_SWITCH", v)
        assert kill_switch_active(tmp_path / "KILL") is False


# --- drawdown halts ---


def test_daily_dd_halt_at_3pct(tmp_path: Path) -> None:
    state = RiskState(
        equity_now=9_700.0,
        daily_high_water=10_000.0,
        weekly_high_water=10_000.0,
        open_positions_count=0,
        open_total_notional_usd=0.0,
    )
    d = check_entry(state, 1000.0, _caps(tmp_path))
    assert d.allow is False
    assert d.reason == "daily_drawdown_halt"


def test_daily_dd_just_under_allows(tmp_path: Path) -> None:
    """2.99% dd → still allowed."""
    state = RiskState(
        equity_now=9_701.0,
        daily_high_water=10_000.0,
        weekly_high_water=10_000.0,
        open_positions_count=0,
        open_total_notional_usd=0.0,
    )
    d = check_entry(state, 1000.0, _caps(tmp_path))
    assert d.allow is True


def test_weekly_dd_halt_at_7pct(tmp_path: Path) -> None:
    state = RiskState(
        equity_now=9_300.0,
        daily_high_water=9_500.0,
        weekly_high_water=10_000.0,
        open_positions_count=0,
        open_total_notional_usd=0.0,
    )
    d = check_entry(state, 1000.0, _caps(tmp_path))
    assert d.allow is False
    assert d.reason == "weekly_drawdown_halt"


# --- concurrency + notional ---


def test_max_positions_blocks_4th(tmp_path: Path) -> None:
    state = RiskState(
        equity_now=10_000.0,
        daily_high_water=10_000.0,
        weekly_high_water=10_000.0,
        open_positions_count=3,
        open_total_notional_usd=10_000.0,
    )
    d = check_entry(state, 1000.0, _caps(tmp_path))
    assert d.allow is False
    assert d.reason == "max_concurrent_positions"


def test_max_position_notional_blocks_oversized(tmp_path: Path) -> None:
    d = check_entry(CLEAN, 50_001.0, _caps(tmp_path))
    assert d.allow is False
    assert d.reason == "max_position_notional"


def test_max_total_notional_blocks_when_aggregate_exceeds(tmp_path: Path) -> None:
    state = RiskState(
        equity_now=10_000.0,
        daily_high_water=10_000.0,
        weekly_high_water=10_000.0,
        open_positions_count=2,
        open_total_notional_usd=80_000.0,
    )
    d = check_entry(state, 30_000.0, _caps(tmp_path))
    assert d.allow is False
    assert d.reason == "max_total_notional"


# --- precedence ---


def test_kill_switch_beats_everything_else(tmp_path: Path) -> None:
    """If kill switch is on, no other condition matters."""
    caps = _caps(tmp_path)
    caps.kill_switch_path.touch()
    catastrophic_state = RiskState(
        equity_now=1_000.0,  # huge dd
        daily_high_water=10_000.0,
        weekly_high_water=10_000.0,
        open_positions_count=99,  # cap exceeded
        open_total_notional_usd=999_999.0,
    )
    d = check_entry(catastrophic_state, 999_999.0, caps)
    assert d.reason == "kill_switch"  # not "daily_drawdown_halt" or anything else
