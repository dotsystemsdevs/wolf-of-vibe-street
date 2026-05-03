"""Shared signal data types — consumed by strategies, executor, and decision log."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SignalSide = Literal["buy", "sell", "hold", "short", "cover"]


@dataclass(frozen=True, slots=True)
class Signal:
    """One signal at one bar.

    `conviction` is in [-1, +1] per S-58 — strategies emit a score, not a quantity;
    sizing is the risk layer's job.

    Sides:
      - `buy`   opens a long position (REQUIRES `stop` per S-15 / P-20)
      - `sell`  closes an open long
      - `short` opens a short position (REQUIRES `stop` ABOVE entry — same rule)
      - `cover` closes an open short
      - `hold`  no action

    Phase 1 added shorts in paper mode (2026-05-03) for bear-regime opportunities.
    Same semantics will translate to perp-futures broker when we promote to live.
    """

    timestamp_ms: int
    symbol: str
    side: SignalSide
    conviction: float
    stop: float | None
    target: float | None
    rationale: str

    def __post_init__(self) -> None:
        if not -1.0 <= self.conviction <= 1.0:
            raise ValueError(f"conviction must be in [-1, 1], got {self.conviction}")
        if self.side in ("buy", "short") and self.stop is None:
            raise ValueError(
                f"{self.side} signal at {self.timestamp_ms} has no stop (S-15)"
            )
