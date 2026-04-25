"""Position sizing — fixed-% risk on stop distance.

Per @design-doc.md §5: 0.5% portfolio risk per trade, hard cap 1%. Risk math is %-based.
"""

from __future__ import annotations

DEFAULT_RISK_PCT = 0.005
MAX_RISK_PCT = 0.01


def position_size(
    equity: float,
    entry_price: float,
    stop_price: float,
    *,
    risk_pct: float = DEFAULT_RISK_PCT,
) -> float:
    """Return quantity such that (entry - stop) * qty == equity * risk_pct.

    Long-only for now: requires `entry_price > stop_price`. Returns 0.0 if any input
    is degenerate (zero equity, zero distance) — the caller should treat 0 as "skip
    this trade", never as an error.
    """
    if equity <= 0:
        return 0.0
    if not 0 < risk_pct <= MAX_RISK_PCT:
        raise ValueError(f"risk_pct must be in (0, {MAX_RISK_PCT}], got {risk_pct}")
    distance = entry_price - stop_price
    if distance <= 0:
        return 0.0
    return (equity * risk_pct) / distance
