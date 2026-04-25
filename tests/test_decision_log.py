"""Tests for memory.decision_log — append, query, and append-only enforcement (I-6)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from memory.decision_log import DecisionEvent, DecisionLog


def _ev(**kw) -> DecisionEvent:
    base: dict = {
        "timestamp_ms": 1000,
        "event_type": "signal",
        "symbol": "BTC/USDT",
        "strategy_id": "baseline",
    }
    base.update(kw)
    return DecisionEvent(**base)


def test_expected_append_then_query(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "log.db")
    log.append(_ev(side="buy", price=100.0))
    log.append(_ev(timestamp_ms=2000, event_type="order_filled", side="buy", price=101.0))

    rows = log.all()
    assert len(rows) == 2
    assert rows[0]["event_type"] == "signal"
    assert rows[1]["event_type"] == "order_filled"
    assert rows[0]["price"] == 100.0


def test_edge_metadata_serialized_to_json(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "log.db")
    log.append(_ev(metadata={"conviction": 0.7, "regime": "high"}))
    rows = log.all()
    assert '"conviction": 0.7' in rows[0]["metadata_json"]


def test_edge_lookup_by_client_order_id(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "log.db")
    log.append(_ev(event_type="order_placed", client_order_id="abc123"))
    log.append(_ev(event_type="order_filled", client_order_id="abc123"))
    log.append(_ev(event_type="signal", client_order_id="other"))

    matches = log.by_client_order_id("abc123")
    assert len(matches) == 2
    assert all(m["client_order_id"] == "abc123" for m in matches)


def test_failure_update_blocked_by_trigger(tmp_path: Path) -> None:
    """I-6: historical rows can never be modified."""
    log = DecisionLog(tmp_path / "log.db")
    log.append(_ev(price=100.0))

    raw = sqlite3.connect(tmp_path / "log.db")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        raw.execute("UPDATE decisions SET price = 999.0 WHERE id = 1")
    raw.close()


def test_failure_delete_blocked_by_trigger(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "log.db")
    log.append(_ev())

    raw = sqlite3.connect(tmp_path / "log.db")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        raw.execute("DELETE FROM decisions WHERE id = 1")
    raw.close()


def test_count_matches_appends(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "log.db")
    for i in range(5):
        log.append(_ev(timestamp_ms=i))
    assert log.count() == 5
