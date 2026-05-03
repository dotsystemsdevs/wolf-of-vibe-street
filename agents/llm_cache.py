"""Persistent verdict cache for LLM evaluations.

The live loop calls `strategy_fn(df)` on every tick — without a cache, every
historical bar with a BUY signal re-hits the API on every tick. With 7 symbols
on a 1h cadence over a few weeks of history, that's hundreds of redundant calls
per tick. The cache keys on (symbol, signal_id, prompt_version) so changing the
prompt invalidates old verdicts cleanly.

Stored as a single JSON file at `data/state/llm_cache.json`. Atomic writes via
tempfile + rename so a crash mid-write can't corrupt the cache.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Any

DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "state" / "llm_cache.json"


class VerdictCache:
    def __init__(self, path: Path = DEFAULT_CACHE_PATH):
        self.path = path
        self._lock = Lock()
        self._data: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _key(self, symbol: str, signal_id: str, prompt_version: str) -> str:
        return f"{symbol}::{signal_id}::{prompt_version}"

    def get(self, symbol: str, signal_id: str, prompt_version: str) -> dict[str, Any] | None:
        return self._data.get(self._key(symbol, signal_id, prompt_version))

    def put(
        self, symbol: str, signal_id: str, prompt_version: str, verdict: Any
    ) -> None:
        """Insert + persist atomically. `verdict` may be a Verdict dataclass or dict."""
        d = asdict(verdict) if hasattr(verdict, "__dataclass_fields__") else dict(verdict)
        with self._lock:
            self._data[self._key(symbol, signal_id, prompt_version)] = d
            self._flush()

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: tempfile in same dir → rename
        tmp = tempfile.NamedTemporaryFile(
            mode="w", dir=self.path.parent, delete=False, suffix=".tmp"
        )
        try:
            json.dump(self._data, tmp)
            tmp.close()
            os.replace(tmp.name, self.path)
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise

    def __len__(self) -> int:
        return len(self._data)
