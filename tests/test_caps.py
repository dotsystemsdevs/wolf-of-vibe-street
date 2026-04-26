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


# --- live presets ---


def test_live_calibration_caps_scale_to_initial_cash() -> None:
    """For 1000 SEK (~$100): max position $25, daily kill $5. Numbers must follow equity."""
    from risk.caps import live_calibration_caps

    c100 = live_calibration_caps(initial_cash_usd=100.0)
    assert c100.max_position_notional_usd == 25.0
    assert c100.max_total_notional_usd == 25.0
    assert c100.max_daily_loss_usd == 5.0
    assert c100.max_concurrent_positions == 1

    c10k = live_calibration_caps(initial_cash_usd=10_000.0)
    assert c10k.max_position_notional_usd == 2_500.0
    assert c10k.max_daily_loss_usd == 500.0


def test_live_full_caps_are_wider_than_calibration() -> None:
    """Full live: 50% per position, 100% total notional, 10% daily kill."""
    from risk.caps import live_calibration_caps, live_full_caps

    cal = live_calibration_caps(initial_cash_usd=100.0)
    full = live_full_caps(initial_cash_usd=100.0)
    assert full.max_position_notional_usd > cal.max_position_notional_usd
    assert full.max_concurrent_positions > cal.max_concurrent_positions
    assert full.max_daily_loss_usd > cal.max_daily_loss_usd
    # Still tighter than the (effectively unlimited) paper defaults.
    assert full.max_daily_loss_usd < float("inf")


def test_max_daily_loss_usd_blocks_new_entry(tmp_path: Path) -> None:
    """Absolute $-loss kill triggers even when % drawdown would still be below cap."""
    from risk.caps import live_calibration_caps

    caps = live_calibration_caps(initial_cash_usd=100.0)
    # Start of day equity 100, now down 6 USD = 6% drawdown — but max_daily_loss_usd=$5
    # should fire FIRST since it's the more conservative cap on this size.
    state = RiskState(
        equity_now=94.0,
        daily_high_water=100.0,
        weekly_high_water=100.0,
        open_positions_count=0,
        open_total_notional_usd=0.0,
    )
    d = check_entry(state, intended_notional_usd=10.0, caps=caps)
    assert d.allow is False
    assert d.reason == "daily_loss_usd_halt"


def test_paper_default_caps_have_no_daily_loss_usd_limit() -> None:
    """Default RiskCaps must keep max_daily_loss_usd=inf so paper isn't blocked."""
    from risk.caps import RiskCaps

    assert RiskCaps().max_daily_loss_usd == float("inf")
