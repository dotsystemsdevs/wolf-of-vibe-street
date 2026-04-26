"""Per-session human gate — defense in depth on top of LIVE_TRADING env flag.

The env flag (`risk.live_gate`) says "the operator configured this machine for
live trading at some point". This module says "the operator just sat down at
the dashboard, typed LIVE, and intends *this session* to place real orders."

Both are required before any KrakenBroker fill can flow:
  env LIVE_TRADING=true ─┐
                         ├─ AND ─→ live order allowed
  session token active  ─┘

Implementation: a file `data/state/LIVE_SESSION_TOKEN` whose mtime is the
session activation time. The loop refuses to construct a non-dry-run live
broker if the file doesn't exist OR is older than `MAX_SESSION_AGE_S`
(default 24 hours). Restarting the loop forces a fresh session token —
operator must re-confirm.

Why a file (not just session_state):
  - Survives browser refresh + dashboard restart.
  - Visible to the live-loop subprocess (separate Python process from the
    Streamlit one, can't share session_state).
  - Easy to "kill switch" the session: rm the file, the next bar gets blocked.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TOKEN_PATH = Path("data/state/LIVE_SESSION_TOKEN")
LIVE_CONFIRMATION_PHRASE = "LIVE"  # exact match required from the operator
MAX_SESSION_AGE_S = 24 * 3600  # 24 hours — operator must re-confirm daily


@dataclass(frozen=True, slots=True)
class SessionState:
    """Snapshot of the live-session gate's current state."""

    is_active: bool
    activated_at_ms: int | None
    expires_at_ms: int | None
    age_s: int | None
    seconds_remaining: int | None


def is_live_session_active(
    token_path: Path = DEFAULT_TOKEN_PATH, *, now_ms: int | None = None
) -> bool:
    """True iff token exists AND is younger than MAX_SESSION_AGE_S."""
    return get_session_state(token_path, now_ms=now_ms).is_active


def get_session_state(
    token_path: Path = DEFAULT_TOKEN_PATH, *, now_ms: int | None = None
) -> SessionState:
    """Inspect the gate state. Used by the sidebar widget + the loop."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    if not token_path.exists():
        return SessionState(
            is_active=False,
            activated_at_ms=None,
            expires_at_ms=None,
            age_s=None,
            seconds_remaining=None,
        )
    activated_ms = int(token_path.stat().st_mtime * 1000)
    age_s = max(0, (now - activated_ms) // 1000)
    expires_ms = activated_ms + MAX_SESSION_AGE_S * 1000
    seconds_remaining = max(0, (expires_ms - now) // 1000)
    is_active = age_s < MAX_SESSION_AGE_S
    return SessionState(
        is_active=is_active,
        activated_at_ms=activated_ms,
        expires_at_ms=expires_ms,
        age_s=age_s,
        seconds_remaining=seconds_remaining,
    )


def activate_live_session(confirmation: str, token_path: Path = DEFAULT_TOKEN_PATH) -> SessionState:
    """Create the token file. Raises ValueError if the confirmation phrase doesn't match.

    The strict-match check (case-sensitive "LIVE") is the entire point — it forces
    the operator to *type* the confirmation, not just click a button. A misclick
    cannot accidentally enable live trading.
    """
    if confirmation != LIVE_CONFIRMATION_PHRASE:
        raise ValueError(
            f"confirmation phrase must be exactly {LIVE_CONFIRMATION_PHRASE!r}, "
            f"got {confirmation!r}"
        )
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.touch()
    return get_session_state(token_path)


def deactivate_live_session(token_path: Path = DEFAULT_TOKEN_PATH) -> None:
    """Remove the token. Idempotent — no-op if file doesn't exist."""
    if token_path.exists():
        token_path.unlink()


def assert_live_session_active(token_path: Path = DEFAULT_TOKEN_PATH) -> None:
    """Raise RuntimeError if there is no fresh session token.

    Called from build_from_env when TRADERBOT_BROKER=kraken and KRAKEN_DRY_RUN
    is false. The error message tells the operator exactly how to remediate:
    open the dashboard, type LIVE in the sidebar gate.
    """
    state = get_session_state(token_path)
    if not state.is_active:
        raise RuntimeError(
            f"No active live-session token at {token_path}. Open the dashboard "
            f"sidebar → 'Live session gate' → type LIVE → activate. The token "
            f"expires after {MAX_SESSION_AGE_S // 3600}h and must be refreshed."
        )
