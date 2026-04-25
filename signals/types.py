"""Shared signal data types — consumed by strategies, executor, and decision log."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SignalSide = Literal["buy", "sell", "hold"]


@dataclass(frozen=True, slots=True)
class Signal:
    """One signal at one bar.

    `conviction` is in [-1, +1] per S-58 — strategies emit a score, not a quantity;
    sizing is the risk layer's job. For Phase 1 (spot, long-only): `buy` opens a
    position and REQUIRES `stop` (S-15 / P-20: no stop = no entry); `sell` closes an
    open position (the stop was set on entry, none needed here).
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
        if self.side == "buy" and self.stop is None:
            raise ValueError(f"buy signal at {self.timestamp_ms} has no stop (S-15)")
