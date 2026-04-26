"""Live-trading gate — the safety interlock between paper and real money.

CLAUDE.md hard rule (§3.2): "Real orders require an explicit `LIVE_TRADING=true`
flag *and* a session-time human gate that does not exist yet."

This module owns the *first* of those two checks (the env flag). The
session-time human gate is built in the dashboard (Phase 3 — sidebar
confirmation widget). Both must return True for a non-paper broker to be
constructible.

Design intent:
- Default-deny. Anything that wants to trade real money must call
  `assert_live_trading_enabled()` before constructing a real broker. If the env
  flag is not set or set to anything other than the literal string "true", the
  call raises and the bot stays paper-only.
- One source of truth. PaperBroker code paths never call this — paper is always
  allowed. Only KrakenBroker (and any future real-money adapter) gates on it.
- Defense in depth: the env flag is necessary but not sufficient. The dashboard
  gate adds a per-session human confirmation on top.
"""

from __future__ import annotations

import os

LIVE_TRADING_ENV_VAR = "LIVE_TRADING"
LIVE_TRADING_OPT_IN_VALUE = "true"  # exact match, lowercase, no whitespace allowance

# The literal modes that get tagged into the decision-log metadata.json on every
# fill. UI banners + later analysis can filter on these:
#   - "paper":            PaperBroker — never a real order.
#   - "live_calibration": real broker, first N trades — small size, gated.
#   - "live":             real broker, post-calibration — full size allowed.
TradeMode = str  # one of: "paper" | "live_calibration" | "live"
PAPER_MODE: TradeMode = "paper"
LIVE_CALIBRATION_MODE: TradeMode = "live_calibration"
LIVE_MODE: TradeMode = "live"

# How many trades the bot must complete in calibration mode before a human can
# promote it to full "live". Comes from S-55 (experiences.md): "first 30 live
# trades are calibration, not P&L". Encoded as a constant so the executor and
# dashboard agree.
CALIBRATION_TRADE_COUNT = 30


def is_live_trading_enabled(env: dict[str, str] | None = None) -> bool:
    """True iff `LIVE_TRADING=true` (lowercase, no whitespace) is set.

    Pass `env` for testability; defaults to `os.environ`. Any other value
    (including "True", "1", "yes", or unset) returns False — the env-flag
    interlock is intentionally strict so a typo can't accidentally enable
    live trading.
    """
    source = env if env is not None else os.environ
    return source.get(LIVE_TRADING_ENV_VAR, "").strip() == LIVE_TRADING_OPT_IN_VALUE


def assert_live_trading_enabled(env: dict[str, str] | None = None) -> None:
    """Raise RuntimeError if the live-trading env flag is not set.

    Called from the constructor of any non-paper broker. Message includes the
    exact env var name + value so the failure is self-explanatory.
    """
    if not is_live_trading_enabled(env):
        raise RuntimeError(
            f"Real-money broker construction requires "
            f"{LIVE_TRADING_ENV_VAR}={LIVE_TRADING_OPT_IN_VALUE!r} in the "
            f"environment. The bot remains paper-only until this flag is set."
        )
