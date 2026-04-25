"""Append-only SQLite decision log (I-6).

Every signal, every order placement, every fill, every risk block, every reject —
they all write a row here. The file lives under `data/decision_log/` (no-touch list,
CLAUDE.md §3.3). Triggers reject UPDATE and DELETE so historical rows can never be
rewritten.

Use `DecisionLog.append(...)` — `query(...)` is for the dashboard / post-mortems.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

EventType = Literal[
    "signal",
    "order_placed",
    "order_filled",
    "order_rejected",
    "risk_block",
    "reconcile_drift",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms    INTEGER NOT NULL,
    event_type      TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    side            TEXT,
    strategy_id     TEXT    NOT NULL,
    signal_id       TEXT,
    client_order_id TEXT,
    price           REAL,
    quantity        REAL,
    notional        REAL,
    pnl             REAL,
    slippage_bps    REAL,
    rationale       TEXT,
    metadata_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts        ON decisions(timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_decisions_coid      ON decisions(client_order_id);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol    ON decisions(symbol);

CREATE TRIGGER IF NOT EXISTS decisions_no_update
    BEFORE UPDATE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'decision_log is append-only (I-6)');
END;

CREATE TRIGGER IF NOT EXISTS decisions_no_delete
    BEFORE DELETE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'decision_log is append-only (I-6)');
END;
"""


@dataclass(frozen=True, slots=True)
class DecisionEvent:
    timestamp_ms: int
    event_type: EventType
    symbol: str
    strategy_id: str
    side: str | None = None
    signal_id: str | None = None
    client_order_id: str | None = None
    price: float | None = None
    quantity: float | None = None
    notional: float | None = None
    pnl: float | None = None
    slippage_bps: float | None = None
    rationale: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class DecisionLog:
    """Thin wrapper around a SQLite table. One row per event, never updated."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.executescript(SCHEMA)

    def append(self, event: DecisionEvent) -> int:
        """Insert one event. Returns the new row id."""
        d = asdict(event)
        meta = d.pop("metadata") or {}
        d["metadata_json"] = json.dumps(meta) if meta else None
        cols = ", ".join(d.keys())
        placeholders = ", ".join(f":{k}" for k in d)
        cur = self._conn.execute(f"INSERT INTO decisions ({cols}) VALUES ({placeholders})", d)
        return int(cur.lastrowid or 0)

    def all(self) -> list[dict[str, Any]]:
        cur = self._conn.execute("SELECT * FROM decisions ORDER BY id ASC")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

    def by_client_order_id(self, coid: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM decisions WHERE client_order_id = ? ORDER BY id ASC", (coid,)
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM decisions")
        return int(cur.fetchone()[0])

    def close(self) -> None:
        self._conn.close()
