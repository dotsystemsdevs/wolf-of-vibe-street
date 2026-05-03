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
    """Return absolute quantity such that |entry - stop| * qty == equity * risk_pct.

    Direction-agnostic: works for both longs (stop below entry) and shorts (stop
    above entry). Returns positive qty in both cases — sign is the executor's
    job. Returns 0.0 if any input is degenerate (zero equity, zero distance)
    so the caller can treat 0 as "skip this trade", never as an error.
    """
    if equity <= 0:
        return 0.0
    if not 0 < risk_pct <= MAX_RISK_PCT:
        raise ValueError(f"risk_pct must be in (0, {MAX_RISK_PCT}], got {risk_pct}")
    distance = abs(entry_price - stop_price)
    if distance <= 0:
        return 0.0
    return (equity * risk_pct) / distance


def vol_targeted_risk_pct(
    *,
    base_risk_pct: float = DEFAULT_RISK_PCT,
    realized_vol: float,
    target_vol: float = 0.04,
    floor: float = 0.001,
    ceiling: float = MAX_RISK_PCT,
) -> float:
    """Scale `base_risk_pct` so each trade contributes a constant ex-ante vol.

    realized_vol is daily stdev of returns (e.g. 0.03 = 3%). target_vol is the
    desired per-trade vol contribution (default 4% — Clenow's CTA preset).

    When the asset is calmer than target → bigger position (larger risk_pct).
    When the asset is wilder → smaller position. Bounded by floor/ceiling so
    a quiet day doesn't lever us into oblivion.
    """
    if realized_vol <= 0:
        return base_risk_pct
    scale = target_vol / realized_vol
    scaled = base_risk_pct * scale
    return max(floor, min(ceiling, scaled))
