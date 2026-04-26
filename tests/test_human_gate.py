"""Tests for risk.human_gate — per-session human confirmation token (P-11 + §3.2).

3-test rule (CLAUDE.md §3.1):
  - expected: type LIVE → token created, gate active
  - edge: token ages out after MAX_SESSION_AGE_S
  - failure: wrong confirmation phrase raises; assert_* raises when inactive
"""

from __future__ import annotations

from pathlib import Path

import pytest

from risk.human_gate import (
    LIVE_CONFIRMATION_PHRASE,
    MAX_SESSION_AGE_S,
    activate_live_session,
    assert_live_session_active,
    deactivate_live_session,
    get_session_state,
    is_live_session_active,
)


def test_expected_activate_creates_token_and_marks_active(tmp_path: Path) -> None:
    token = tmp_path / "TOKEN"
    state = activate_live_session(LIVE_CONFIRMATION_PHRASE, token)
    assert token.exists()
    assert state.is_active is True
    assert state.activated_at_ms is not None
    assert state.seconds_remaining is not None
    assert state.seconds_remaining > 0
    assert is_live_session_active(token)


def test_failure_wrong_confirmation_raises(tmp_path: Path) -> None:
    token = tmp_path / "TOKEN"
    with pytest.raises(ValueError, match=LIVE_CONFIRMATION_PHRASE):
        activate_live_session("live", token)  # lowercase — strict mismatch
    with pytest.raises(ValueError):
        activate_live_session("YES", token)
    assert not token.exists()
    assert not is_live_session_active(token)


def test_edge_expired_token_is_inactive(tmp_path: Path) -> None:
    """Token older than MAX_SESSION_AGE_S must be treated as inactive."""
    token = tmp_path / "TOKEN"
    activate_live_session(LIVE_CONFIRMATION_PHRASE, token)
    # Simulate "now is 25 hours after activation" by passing a future now_ms.
    real_activated_ms = get_session_state(token).activated_at_ms
    assert real_activated_ms is not None
    future_ms = real_activated_ms + (MAX_SESSION_AGE_S + 1) * 1000
    state = get_session_state(token, now_ms=future_ms)
    assert state.is_active is False
    assert state.age_s is not None and state.age_s > MAX_SESSION_AGE_S
    assert state.seconds_remaining == 0


def test_deactivate_removes_token_idempotent(tmp_path: Path) -> None:
    token = tmp_path / "TOKEN"
    activate_live_session(LIVE_CONFIRMATION_PHRASE, token)
    deactivate_live_session(token)
    assert not token.exists()
    deactivate_live_session(token)  # idempotent, no error
    assert not is_live_session_active(token)


def test_failure_assert_raises_when_no_token(tmp_path: Path) -> None:
    token = tmp_path / "MISSING"
    with pytest.raises(RuntimeError, match="live-session token"):
        assert_live_session_active(token)


def test_failure_assert_raises_when_token_expired(tmp_path: Path) -> None:
    token = tmp_path / "TOKEN"
    activate_live_session(LIVE_CONFIRMATION_PHRASE, token)
    # Backdate the file to before the window.
    import os

    old_time = get_session_state(token).activated_at_ms / 1000 - MAX_SESSION_AGE_S - 60
    os.utime(token, (old_time, old_time))
    with pytest.raises(RuntimeError):
        assert_live_session_active(token)
