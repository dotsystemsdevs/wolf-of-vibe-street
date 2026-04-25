"""Tests for tools.loop_control — status + dead-PID cleanup + tail. No real subprocess."""

from __future__ import annotations

import os
from pathlib import Path

from tools.loop_control import LoopStatus, status, tail_log


def test_status_no_pidfile_returns_not_running(tmp_path: Path) -> None:
    s = status(pid_path=tmp_path / "loop.pid", log_path=tmp_path / "loop.log")
    assert s.running is False
    assert s.pid is None


def test_status_dead_pid_cleans_up(tmp_path: Path) -> None:
    """A PID that doesn't exist on the system → file gets unlinked, status=not running."""
    pid_path = tmp_path / "loop.pid"
    pid_path.write_text("99999999")  # almost certainly not a real PID
    s = status(pid_path=pid_path, log_path=tmp_path / "loop.log")
    assert s.running is False
    assert pid_path.exists() is False  # auto-cleaned


def test_status_garbage_pidfile_cleans_up(tmp_path: Path) -> None:
    pid_path = tmp_path / "loop.pid"
    pid_path.write_text("not-a-number")
    s = status(pid_path=pid_path, log_path=tmp_path / "loop.log")
    assert s.running is False
    assert pid_path.exists() is False


def test_status_alive_pid_reports_running(tmp_path: Path) -> None:
    """Use our own PID — guaranteed to be alive while the test runs."""
    pid_path = tmp_path / "loop.pid"
    pid_path.write_text(str(os.getpid()))
    s = status(pid_path=pid_path, log_path=tmp_path / "loop.log")
    assert s.running is True
    assert s.pid == os.getpid()
    assert s.started_at_ms is not None


def test_tail_log_empty_when_missing(tmp_path: Path) -> None:
    assert tail_log(tmp_path / "missing.log") == ""


def test_tail_log_returns_last_n_lines(tmp_path: Path) -> None:
    log = tmp_path / "loop.log"
    log.write_text("\n".join(f"line {i}" for i in range(100)) + "\n")

    out = tail_log(log, lines=5)
    assert out.splitlines() == ["line 95", "line 96", "line 97", "line 98", "line 99"]


def test_tail_log_handles_short_file(tmp_path: Path) -> None:
    log = tmp_path / "loop.log"
    log.write_text("only one line\n")
    assert "only one line" in tail_log(log, lines=50)


def test_loop_status_running_property() -> None:
    s = LoopStatus(pid=None, started_at_ms=None, log_path=Path("/tmp/x"), pid_path=Path("/tmp/y"))
    assert s.running is False
    s2 = LoopStatus(pid=42, started_at_ms=1000, log_path=Path("/tmp/x"), pid_path=Path("/tmp/y"))
    assert s2.running is True


# --- reset_decision_log ---


def test_reset_decision_log_moves_db_to_backup(tmp_path: Path) -> None:
    from tools.loop_control import reset_decision_log

    db = tmp_path / "traderbot.db"
    db.write_bytes(b"fake-sqlite-content")

    backup = reset_decision_log(db, timestamp="20260425T211900")
    assert backup is not None
    assert backup.name == "traderbot.db-20260425T211900.bak"
    assert backup.exists()
    assert backup.read_bytes() == b"fake-sqlite-content"
    assert db.exists() is False  # original moved aside


def test_reset_decision_log_missing_db_returns_none(tmp_path: Path) -> None:
    from tools.loop_control import reset_decision_log

    assert reset_decision_log(tmp_path / "nope.db") is None


def test_reset_decision_log_creates_backups_dir(tmp_path: Path) -> None:
    from tools.loop_control import reset_decision_log

    db = tmp_path / "traderbot.db"
    db.write_bytes(b"x")
    backup = reset_decision_log(db, timestamp="20260101T000000")
    assert backup is not None
    assert backup.parent.name == "backups"
    assert backup.parent.exists()
