"""Risk caps + kill switch — enforced in `execution/` (I-5) regardless of strategy.

Per @design-doc.md §5:
- Default size 0.5% portfolio risk / trade, hard cap 1% (handled in `risk/sizing.py`).
- Max daily DD 3 %, weekly DD 7 % → halt new orders.
- Max 3 concurrent positions in v1.
- Kill switch: env `KILL_SWITCH=true` OR a sentinel file → halt all new orders.

This module provides the *check* used by the executor. It is intentionally pure:
the executor passes in a `RiskState` snapshot; the function returns a `RiskDecision`.
No I/O here except for the kill-switch file existence check.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_KILL_SWITCH_PATH = Path("data/state/KILL_SWITCH")


@dataclass(frozen=True, slots=True)
class RiskCaps:
    max_concurrent_positions: int = 3
    max_position_notional_usd: float = 50_000.0
    max_total_notional_usd: float = 100_000.0
    max_daily_drawdown_pct: float = 0.03
    max_weekly_drawdown_pct: float = 0.07
    # Absolute-dollar daily-loss kill, separate from the %-based DD halt. The
    # %-cap can be too generous on small accounts (2% of $100 = $2, fine; 2%
    # of $10k = $200, hurts). The $-cap stops bleeding regardless of equity.
    # Default inf = paper mode; live presets tighten it to small numbers.
    max_daily_loss_usd: float = float("inf")
    kill_switch_path: Path = DEFAULT_KILL_SWITCH_PATH


def live_calibration_caps(
    initial_cash_usd: float = 100.0,
    *,
    kill_switch_path: Path = DEFAULT_KILL_SWITCH_PATH,
) -> RiskCaps:
    """Hardened caps for the live-calibration phase (S-55: first 30 trades).

    Sized as fractions of `initial_cash_usd` so the same preset works whether
    the operator funds with 1000 SEK (~$100), $500, or $10k. Defaults match
    the 1000 SEK / 30-day "what does my AI bot do?" journey: tight enough
    that a single bad fill can't blow the account, loose enough to let real
    edge become visible.

      - max_position_notional_usd: 25% of equity — meaningful position size
        without YOLO. On $100: max $25 / trade.
      - max_total_notional_usd: same as per-position (1 concurrent).
      - max_concurrent_positions: 1 (clean attribution; no portfolio drag).
      - max_daily_drawdown_pct: 3% — same as paper; small accounts already
        get squeezed by absolute $-cap below.
      - max_weekly_drawdown_pct: 7%.
      - max_daily_loss_usd: 5% of equity — absolute floor in dollars. On
        $100 = $5 daily kill, on $10k = $500.
    """
    return RiskCaps(
        max_concurrent_positions=1,
        max_position_notional_usd=initial_cash_usd * 0.25,
        max_total_notional_usd=initial_cash_usd * 0.25,
        max_daily_drawdown_pct=0.03,
        max_weekly_drawdown_pct=0.07,
        max_daily_loss_usd=initial_cash_usd * 0.05,
        kill_switch_path=kill_switch_path,
    )


def live_full_caps(
    initial_cash_usd: float = 100.0,
    *,
    kill_switch_path: Path = DEFAULT_KILL_SWITCH_PATH,
) -> RiskCaps:
    """Post-calibration caps — promoted from calibration after the first 30 trades.

    Wider than calibration, still tighter than paper. Numbers are conservative
    starting points; revisit after ~100 live trades give actual fill-quality
    data. On $100 default: 50% per-position, $10 daily kill.
    """
    return RiskCaps(
        max_concurrent_positions=2,
        max_position_notional_usd=initial_cash_usd * 0.50,
        max_total_notional_usd=initial_cash_usd * 1.00,
        max_daily_drawdown_pct=0.025,
        max_weekly_drawdown_pct=0.06,
        max_daily_loss_usd=initial_cash_usd * 0.10,
        kill_switch_path=kill_switch_path,
    )


@dataclass(frozen=True, slots=True)
class RiskState:
    equity_now: float
    daily_high_water: float
    weekly_high_water: float
    open_positions_count: int
    open_total_notional_usd: float


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allow: bool
    reason: str


_OK = RiskDecision(True, "ok")
_DEFAULT_CAPS = RiskCaps()


def kill_switch_active(path: Path = DEFAULT_KILL_SWITCH_PATH) -> bool:
    """True if env `KILL_SWITCH=true` (case-insensitive) OR `path` exists."""
    if os.environ.get("KILL_SWITCH", "").strip().lower() == "true":
        return True
    return path.exists()


def check_entry(
    state: RiskState,
    intended_notional_usd: float,
    caps: RiskCaps | None = None,
) -> RiskDecision:
    """Decide whether a *new entry* is allowed. Order of checks is intentional:

    1. Kill switch — fastest possible exit; nothing else matters.
    2. Drawdown halts — if we're already bleeding, don't add risk.
    3. Concurrency cap — refuse new positions when full.
    4. Notional cap — per-position and aggregate.

    Caller (the executor) is responsible for *exits* — risk caps don't block exits,
    only entries. A blocked exit could trap an account in a losing position.
    """
    caps = caps or _DEFAULT_CAPS
    if kill_switch_active(caps.kill_switch_path):
        return RiskDecision(False, "kill_switch")

    if state.daily_high_water > 0:
        daily_loss_usd = state.daily_high_water - state.equity_now
        if daily_loss_usd >= caps.max_daily_loss_usd:
            return RiskDecision(False, "daily_loss_usd_halt")
        daily_dd = daily_loss_usd / state.daily_high_water
        if daily_dd >= caps.max_daily_drawdown_pct:
            return RiskDecision(False, "daily_drawdown_halt")

    if state.weekly_high_water > 0:
        weekly_dd = (state.weekly_high_water - state.equity_now) / state.weekly_high_water
        if weekly_dd >= caps.max_weekly_drawdown_pct:
            return RiskDecision(False, "weekly_drawdown_halt")

    if state.open_positions_count >= caps.max_concurrent_positions:
        return RiskDecision(False, "max_concurrent_positions")

    if intended_notional_usd > caps.max_position_notional_usd:
        return RiskDecision(False, "max_position_notional")

    if state.open_total_notional_usd + intended_notional_usd > caps.max_total_notional_usd:
        return RiskDecision(False, "max_total_notional")

    return _OK
