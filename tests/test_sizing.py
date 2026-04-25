"""Tests for risk.sizing."""

from __future__ import annotations

import pytest

from risk.sizing import position_size


def test_expected_size_matches_risk_budget() -> None:
    """1% equity at risk on $1 stop distance → 100 shares for $10k."""
    qty = position_size(equity=10_000.0, entry_price=100.0, stop_price=99.0, risk_pct=0.01)
    assert qty == pytest.approx(100.0)


def test_edge_zero_distance_returns_zero() -> None:
    """Stop at entry → infinite size would be needed → return 0 (skip)."""
    assert position_size(10_000.0, 100.0, 100.0) == 0.0


def test_edge_zero_equity_returns_zero() -> None:
    assert position_size(0.0, 100.0, 99.0) == 0.0


def test_failure_risk_above_cap_raises() -> None:
    """Hard cap at 1%."""
    with pytest.raises(ValueError):
        position_size(10_000.0, 100.0, 99.0, risk_pct=0.02)
