"""Tests for tools.counterfactual — A/B comparison engine."""

from __future__ import annotations

from tools.counterfactual import SymbolComparison


def test_swap_recommended_when_delta_above_threshold_and_enough_trades() -> None:
    """≥1pp better return AND ≥10 trades on the alternative → recommend swap."""
    c = SymbolComparison(
        symbol="BTC/USDT",
        current_strategy="dipbuy",
        current_return_pct=0.0,
        current_trades=12,
        candidates={
            "dipbuy": {"return_pct": 0.0, "trades": 12, "pf": 1.0},
            "union": {"return_pct": 1.5, "trades": 15, "pf": 1.4},
        },
        best_strategy="union",
        best_return_pct=1.5,
        delta_vs_current=1.5,
    )
    assert c.recommend_swap is True


def test_swap_blocked_by_small_sample_on_alternative() -> None:
    """Big delta but only 3 trades on the alternative → don't trust it yet."""
    c = SymbolComparison(
        symbol="BTC/USDT",
        current_strategy="dipbuy",
        current_return_pct=0.0,
        current_trades=20,
        candidates={
            "dipbuy": {"return_pct": 0.0, "trades": 20, "pf": 1.0},
            "ensemble": {"return_pct": 5.0, "trades": 3, "pf": 4.0},
        },
        best_strategy="ensemble",
        best_return_pct=5.0,
        delta_vs_current=5.0,
    )
    assert c.recommend_swap is False


def test_swap_blocked_when_delta_below_threshold() -> None:
    """≥10 trades on the alternative but only +0.5pp better → not enough signal."""
    c = SymbolComparison(
        symbol="BTC/USDT",
        current_strategy="dipbuy",
        current_return_pct=0.0,
        current_trades=15,
        candidates={
            "dipbuy": {"return_pct": 0.0, "trades": 15, "pf": 1.0},
            "union": {"return_pct": 0.5, "trades": 18, "pf": 1.2},
        },
        best_strategy="union",
        best_return_pct=0.5,
        delta_vs_current=0.5,
    )
    assert c.recommend_swap is False


def test_no_candidates_returns_false() -> None:
    """Edge case — empty candidate set must not crash recommend_swap."""
    c = SymbolComparison(
        symbol="BTC/USDT",
        current_strategy="dipbuy",
        current_return_pct=0.0,
        current_trades=0,
        candidates={},
        best_strategy="dipbuy",
        best_return_pct=0.0,
        delta_vs_current=0.0,
    )
    assert c.recommend_swap is False
