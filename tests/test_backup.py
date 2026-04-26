"""Tests for tools.backup — decision-log backup + retention pruning.

3-test rule (CLAUDE.md §3.1):
  - expected: existing log → timestamped copy in backup_dir, list_backups sees it
  - edge: missing source / empty source → BackupResult.skipped_reason set
  - failure(retention): N+1 backups exist → oldest pruned, newest kept
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tools.backup import backup_decision_log, list_backups


def test_expected_backup_creates_timestamped_copy(tmp_path: Path) -> None:
    src = tmp_path / "traderbot.db"
    src.write_bytes(b"sqlite-bytes")
    backup_dir = tmp_path / "backups"
    fixed_now = datetime(2026, 4, 26, 9, 30, 0, tzinfo=UTC)

    result = backup_decision_log(src, backup_dir=backup_dir, now=fixed_now)
    assert result.ok
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert "20260426-093000" in result.backup_path.name
    assert result.skipped_reason is None
    listed = list_backups(backup_dir)
    assert len(listed) == 1
    assert listed[0] == result.backup_path


def test_edge_missing_source_returns_skipped(tmp_path: Path) -> None:
    src = tmp_path / "missing.db"
    backup_dir = tmp_path / "backups"
    result = backup_decision_log(src, backup_dir=backup_dir)
    assert not result.ok
    assert result.skipped_reason is not None
    assert "does not exist" in result.skipped_reason
    assert not backup_dir.exists()  # we don't make dirs for nothing


def test_edge_empty_source_returns_skipped(tmp_path: Path) -> None:
    src = tmp_path / "traderbot.db"
    src.touch()  # exists but 0 bytes
    backup_dir = tmp_path / "backups"
    result = backup_decision_log(src, backup_dir=backup_dir)
    assert not result.ok
    assert "empty" in (result.skipped_reason or "")


def test_retention_prunes_oldest_when_over_keep(tmp_path: Path) -> None:
    """3 backups with keep=2 → oldest pruned, newest 2 kept."""
    src = tmp_path / "traderbot.db"
    src.write_bytes(b"x")
    backup_dir = tmp_path / "backups"
    # Create 3 backups at distinct timestamps. Sorting is by file mtime, so
    # we sleep-equivalent by setting explicit `now` values.
    t1 = datetime(2026, 4, 26, 8, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 4, 26, 9, 0, 0, tzinfo=UTC)
    t3 = datetime(2026, 4, 26, 10, 0, 0, tzinfo=UTC)
    r1 = backup_decision_log(src, backup_dir=backup_dir, retention_count=2, now=t1)
    r2 = backup_decision_log(src, backup_dir=backup_dir, retention_count=2, now=t2)
    # mtime is "now" of file copy, so we need to backdate the older files for
    # list_backups (sorted by mtime) to order them correctly.
    import os

    os.utime(r1.backup_path, (t1.timestamp(), t1.timestamp()))  # type: ignore[union-attr]
    os.utime(r2.backup_path, (t2.timestamp(), t2.timestamp()))  # type: ignore[union-attr]

    r3 = backup_decision_log(src, backup_dir=backup_dir, retention_count=2, now=t3)
    assert r3.ok
    # r1 should have been pruned. r2 + r3 should remain.
    listed = list_backups(backup_dir)
    listed_names = {p.name for p in listed}
    assert len(listed) == 2
    assert r3.backup_path.name in listed_names  # type: ignore[union-attr]
    assert r2.backup_path.name in listed_names  # type: ignore[union-attr]
    assert r1.backup_path.name not in listed_names  # type: ignore[union-attr]
    assert any(p.name == r1.backup_path.name for p in r3.pruned_paths)  # type: ignore[union-attr]
