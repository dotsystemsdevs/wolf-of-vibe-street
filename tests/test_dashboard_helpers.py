"""Tests for pure helpers in ui.dashboard.

Streamlit-rendering code is excluded — only the data-shape helpers that we
import + call directly. Today: _live_calibration_fill_count, _go_live_readiness.
"""

from __future__ import annotations

import json

from ui.dashboard import _go_live_readiness, _live_calibration_fill_count


def _fill_row(mode: str | None, ts: int = 0) -> dict:
    meta = {"mode": mode, "fee": 0.0} if mode else {"fee": 0.0}
    return {
        "id": ts,
        "timestamp_ms": ts,
        "event_type": "order_filled",
        "symbol": "BTC/USDT",
        "side": "buy",
        "quantity": 0.01,
        "price": 50_000.0,
        "rationale": "",
        "metadata_json": json.dumps(meta),
    }


def _signal_row(ts: int = 0) -> dict:
    return {
        "id": ts,
        "timestamp_ms": ts,
        "event_type": "signal",
        "symbol": "BTC/USDT",
        "side": "buy",
        "rationale": "",
        "metadata_json": None,
    }


def test_calibration_count_only_includes_live_calibration_fills() -> None:
    rows = [
        _signal_row(1),  # signals don't count
        _fill_row("paper", 2),  # wrong mode
        _fill_row("live_calibration", 3),  # ✓
        _fill_row("live_calibration", 4),  # ✓
        _fill_row("live", 5),  # post-calibration, doesn't count
        _fill_row(None, 6),  # missing mode → doesn't count
    ]
    assert _live_calibration_fill_count(rows) == 2


def test_calibration_count_zero_for_empty_log() -> None:
    assert _live_calibration_fill_count([]) == 0


def test_calibration_count_handles_malformed_metadata_json() -> None:
    rows = [
        {
            "id": 1,
            "timestamp_ms": 1,
            "event_type": "order_filled",
            "symbol": "BTC/USDT",
            "side": "buy",
            "rationale": "",
            "metadata_json": "{not valid json",  # garbage
        },
        _fill_row("live_calibration", 2),
    ]
    # Garbage row is skipped silently; the valid one still counts.
    assert _live_calibration_fill_count(rows) == 1


def test_go_live_readiness_returns_11_items() -> None:
    """All 11 items present + each has the required keys + valid status enum."""
    checks = _go_live_readiness(
        rows=[],
        loop_running=False,
        loop_started_at_ms=None,
        env={},
        now_ms=0,
    )
    assert len(checks) == 11
    valid_statuses = {"done", "in_progress", "todo"}
    for c in checks:
        assert {"key", "name", "status", "detail"}.issubset(c.keys())
        assert c["status"] in valid_statuses


def test_go_live_readiness_live_flag_done_when_env_set() -> None:
    checks = _go_live_readiness(
        rows=[],
        loop_running=False,
        loop_started_at_ms=None,
        env={"LIVE_TRADING": "true"},
        now_ms=0,
    )
    flag_check = next(c for c in checks if c["key"] == "live_flag")
    assert flag_check["status"] == "done"


def test_go_live_readiness_telegram_done_when_both_set() -> None:
    checks = _go_live_readiness(
        rows=[],
        loop_running=False,
        loop_started_at_ms=None,
        env={"TELEGRAM_BOT_TOKEN": "abc", "TELEGRAM_CHAT_ID": "123"},
        now_ms=0,
    )
    tg_check = next(c for c in checks if c["key"] == "telegram")
    assert tg_check["status"] == "done"


def test_go_live_readiness_soak_progress_is_in_progress_when_running() -> None:
    """Loop running 1h ago → soak should be in_progress with hours-elapsed detail."""
    one_hour_ago_ms = 1_000_000_000_000  # arbitrary
    now_ms = one_hour_ago_ms + 3_600_000
    checks = _go_live_readiness(
        rows=[],
        loop_running=True,
        loop_started_at_ms=one_hour_ago_ms,
        env={},
        now_ms=now_ms,
    )
    soak_check = next(c for c in checks if c["key"] == "soak")
    assert soak_check["status"] == "in_progress"
    assert "1h" in soak_check["detail"] or "0h" in soak_check["detail"]
