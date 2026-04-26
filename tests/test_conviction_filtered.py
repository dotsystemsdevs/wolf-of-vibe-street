"""Tests for strategies.conviction_filtered.

3-test rule (CLAUDE.md §3.1):
  - expected: high-conviction buy passes through
  - edge: low-conviction buy → hold with rejection rationale
  - failure(non-buy): sell + hold pass through unchanged
"""

from __future__ import annotations

from signals.types import Signal
from strategies.conviction_filtered import make_conviction_filtered

HOUR_MS = 3_600_000


def _stub_strategy(rows):
    """Returns whatever Signals you stuff in. Lets us test the wrapper deterministically."""

    def fn(df, *, symbol="BTC/USDT"):
        return rows

    fn.__name__ = "stub"
    return fn


def test_expected_high_conviction_buy_passes_through() -> None:
    sig = Signal(0, "BTC/USDT", "buy", conviction=0.9, stop=95.0, target=110.0, rationale="x")
    wrapped = make_conviction_filtered(_stub_strategy([sig]), threshold=0.5)
    out = wrapped(df=None)  # df unused by stub
    assert len(out) == 1
    assert out[0].side == "buy"
    assert out[0].stop == 95.0
    assert "conv" in out[0].rationale


def test_edge_low_conviction_buy_becomes_hold_with_reason() -> None:
    sig = Signal(0, "BTC/USDT", "buy", conviction=0.2, stop=95.0, target=110.0, rationale="x")
    wrapped = make_conviction_filtered(_stub_strategy([sig]), threshold=0.5)
    out = wrapped(df=None)
    assert out[0].side == "hold"
    assert out[0].stop is None
    assert "filtered" in out[0].rationale


def test_failure_sell_and_hold_pass_through_unchanged() -> None:
    """Filter only touches buys. Sells (exits) and holds must be untouched."""
    sells_holds = [
        Signal(0, "BTC/USDT", "sell", conviction=0.0, stop=None, target=None, rationale="exit"),
        Signal(HOUR_MS, "BTC/USDT", "hold", conviction=0.0, stop=None, target=None, rationale=""),
    ]
    wrapped = make_conviction_filtered(_stub_strategy(sells_holds), threshold=0.99)
    out = wrapped(df=None)
    assert out[0].side == "sell"
    assert out[1].side == "hold"
    # Rationale strings must be unchanged.
    assert out[0].rationale == "exit"
    assert out[1].rationale == ""
