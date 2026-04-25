"""Start / stop / inspect the live-loop subprocess from the dashboard.

The loop is spawned with `start_new_session=True` so it survives the dashboard
process exiting (close the browser, kill streamlit — loop keeps running). The PID
is persisted to `data/state/loop.pid`; status checks consult `os.kill(pid, 0)` and
auto-clean a stale PID file if the process died.

stdout/stderr is redirected to `data/state/loop.log` for the dashboard to tail.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PID_PATH = Path("data/state/loop.pid")
DEFAULT_LOG_PATH = Path("data/state/loop.log")
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class LoopStatus:
    pid: int | None  # None = not running
    started_at_ms: int | None
    log_path: Path
    pid_path: Path

    @property
    def running(self) -> bool:
        return self.pid is not None


def _process_alive(pid: int) -> bool:
    """POSIX: signal 0 raises ProcessLookupError if pid is dead, no-op if alive."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user — still "alive" from our POV.
        return True


def status(pid_path: Path = DEFAULT_PID_PATH, log_path: Path = DEFAULT_LOG_PATH) -> LoopStatus:
    """Return current loop status. If the PID file points at a dead PID, clean it up."""
    if not pid_path.exists():
        return LoopStatus(None, None, log_path, pid_path)
    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        pid_path.unlink(missing_ok=True)
        return LoopStatus(None, None, log_path, pid_path)

    if not _process_alive(pid):
        pid_path.unlink(missing_ok=True)
        return LoopStatus(None, None, log_path, pid_path)

    started_at_ms = int(pid_path.stat().st_mtime * 1000)
    return LoopStatus(pid, started_at_ms, log_path, pid_path)


def _build_command(use_caffeinate: bool) -> list[str]:
    """Resolve the absolute paths so subprocess works regardless of cwd."""
    cmd: list[str] = []
    if use_caffeinate and shutil.which("caffeinate"):
        cmd.extend(["caffeinate", "-di"])
    uv = shutil.which("uv") or "uv"
    cmd.extend([uv, "run", "python", "-m", "workers.live_loop"])
    return cmd


def start(
    *,
    pid_path: Path = DEFAULT_PID_PATH,
    log_path: Path = DEFAULT_LOG_PATH,
    project_root: Path = DEFAULT_PROJECT_ROOT,
    use_caffeinate: bool = True,
    extra_env: dict[str, str] | None = None,
) -> LoopStatus:
    """Spawn the loop as a detached subprocess. Idempotent: returns existing status if already running."""
    cur = status(pid_path, log_path)
    if cur.running:
        return cur

    pid_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Stays open for the subprocess's lifetime; closing here would close subprocess stdout.
    log_fh = open(log_path, "ab")  # noqa: SIM115
    # PYTHONUNBUFFERED=1 → log tail in the dashboard updates in real time.
    env = {**os.environ, "PYTHONUNBUFFERED": "1", **(extra_env or {})}

    proc = subprocess.Popen(
        _build_command(use_caffeinate),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        cwd=str(project_root),
        env=env,
        start_new_session=True,
    )
    pid_path.write_text(str(proc.pid))

    # Brief check to surface immediate failures (e.g. uv not found).
    time.sleep(0.4)
    if not _process_alive(proc.pid):
        pid_path.unlink(missing_ok=True)
        return LoopStatus(None, None, log_path, pid_path)

    return LoopStatus(proc.pid, int(time.time() * 1000), log_path, pid_path)


def stop(
    *,
    pid_path: Path = DEFAULT_PID_PATH,
    log_path: Path = DEFAULT_LOG_PATH,
    grace_seconds: float = 5.0,
) -> bool:
    """Send SIGTERM to the loop's process group; wait grace_seconds; SIGKILL the PID directly.

    Process-group kill catches caffeinate + uv + python in one shot. If pgid lookup fails
    (process already dying / ours / etc.), fall back to direct-PID kill. SIGKILL/EPERM
    is treated as "the process group includes something we can't touch" — we still unlink
    the PID file and report success; the loop's main process is what we actually care about.
    """
    cur = status(pid_path, log_path)
    if not cur.running:
        return False
    pid = cur.pid
    assert pid is not None

    pgid: int | None = None
    with contextlib.suppress(ProcessLookupError, OSError):
        pgid = os.getpgid(pid)

    def _send(sig: int) -> None:
        # Try the process group first; fall back to the direct PID if EPERM.
        try:
            if pgid is not None and pgid > 0:
                os.kill(-pgid, sig)
                return
        except (ProcessLookupError, PermissionError):
            pass
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, sig)

    _send(signal.SIGTERM)
    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        if not _process_alive(pid):
            pid_path.unlink(missing_ok=True)
            return True
        time.sleep(0.1)

    _send(signal.SIGKILL)
    # Best-effort: unlink even if SIGKILL didn't reach the leader (zombie / orphaned).
    pid_path.unlink(missing_ok=True)
    return True


def tail_log(log_path: Path = DEFAULT_LOG_PATH, lines: int = 50) -> str:
    """Return the last `lines` lines of the log file. Empty string if no log."""
    if not log_path.exists():
        return ""
    try:
        # Read the tail efficiently for large files: seek from end.
        size = log_path.stat().st_size
        with open(log_path, "rb") as f:
            chunk = min(size, max(8192, lines * 200))
            f.seek(-chunk, os.SEEK_END)
            data = f.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-lines:])
    except OSError:
        return ""
