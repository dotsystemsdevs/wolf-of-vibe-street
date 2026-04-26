"""Decision-log backup — small, safe, append-only artefacts.

The decision log is the only thing the bot can't reconstruct. It carries every
fill, every signal, every risk-block — losing it loses our entire audit trail.
SQLite is single-file, so backing up is a file copy with a timestamped name.

Three modes (all use the same mechanism):
  - manual: operator clicks "Backup now" in the sidebar.
  - on-loop-start: build_from_env can call this before the live loop opens
    the DB, giving us a clean restart point.
  - retention: we keep the N most recent backups and prune older ones, so
    the backup directory doesn't grow unbounded over a 30-day run.

We deliberately do NOT compress or encrypt — the file is small (KBs to a few
MB at most over weeks), and a plain SQLite copy is what an operator wants to
inspect with `sqlite3` if something goes wrong.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_BACKUP_DIR = Path("data/decision_log/backups")
RETENTION_COUNT = 30  # keep the N most recent; older are pruned at backup time


@dataclass(frozen=True, slots=True)
class BackupResult:
    backup_path: Path | None
    pruned_paths: list[Path]
    skipped_reason: str | None  # set when nothing to back up

    @property
    def ok(self) -> bool:
        return self.backup_path is not None


def backup_decision_log(
    log_path: Path,
    *,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    retention_count: int = RETENTION_COUNT,
    now: datetime | None = None,
) -> BackupResult:
    """Copy `log_path` to `backup_dir` with a UTC-timestamped name; prune old ones.

    Returns a BackupResult with the new file's path + list of pruned paths.
    `skipped_reason` is set (and backup_path=None) if the source doesn't
    exist or is empty.
    """
    if not log_path.exists():
        return BackupResult(None, [], "source log file does not exist")
    if log_path.stat().st_size == 0:
        return BackupResult(None, [], "source log file is empty (0 bytes)")

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{log_path.stem}_{timestamp}{log_path.suffix}"
    shutil.copy2(log_path, dest)

    pruned = _prune_old_backups(backup_dir, log_path.stem, retention_count)
    return BackupResult(backup_path=dest, pruned_paths=pruned, skipped_reason=None)


def list_backups(
    backup_dir: Path = DEFAULT_BACKUP_DIR, *, log_stem: str = "traderbot"
) -> list[Path]:
    """Newest-first list of existing backup files for the given log stem."""
    if not backup_dir.exists():
        return []
    matches = sorted(
        (p for p in backup_dir.iterdir() if p.is_file() and p.name.startswith(log_stem + "_")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches


def _prune_old_backups(backup_dir: Path, log_stem: str, keep: int) -> list[Path]:
    backups = list_backups(backup_dir, log_stem=log_stem)
    if len(backups) <= keep:
        return []
    to_remove = backups[keep:]
    for p in to_remove:
        try:
            p.unlink()
        except OSError:
            # If we can't delete one, keep going — we don't want to abort the
            # whole backup operation over a permission error on one stale file.
            continue
    return to_remove
