"""Manage the daily-summary crontab entry from the Settings UI.

Identifies our row by a `# traderbot-daily-summary` marker so we never touch
unrelated user cron jobs. Idempotent: install/uninstall can be called multiple
times safely.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = "# traderbot-daily-summary"
UV_BIN = "/opt/homebrew/bin/uv"  # macOS Homebrew default; cron has minimal PATH
LOG_PATH = "/tmp/traderbot_summary.log"


@dataclass(frozen=True)
class CronStatus:
    installed: bool
    hour: int
    minute: int


def _read_crontab() -> str:
    res = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    # `crontab -l` exits 1 when there's no crontab; that's not an error for us.
    return res.stdout if res.returncode == 0 else ""


def _write_crontab(content: str) -> None:
    res = subprocess.run(
        ["crontab", "-"],
        input=content,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"crontab install failed: {res.stderr.strip() or res.stdout.strip()}")


def _strip_our_lines(content: str) -> str:
    """Remove any line ending in our marker. Preserve everything else."""
    out: list[str] = []
    for line in content.splitlines():
        if MARKER in line:
            continue
        out.append(line)
    # Preserve trailing newline behavior — cron expects a newline-terminated file.
    text = "\n".join(out)
    return text + "\n" if text and not text.endswith("\n") else text


def _build_line(hours: list[int] | int, minute: int) -> str:
    """Build a crontab line. `hours` may be a single int or a list; the latter
    becomes a comma-list `H1,H2,H3` so cron fires at every listed hour."""
    if isinstance(hours, int):
        hour_field = str(hours)
    else:
        hour_field = ",".join(str(h) for h in sorted(set(hours)))
    return (
        f"{minute} {hour_field} * * * cd {ROOT} && {UV_BIN} run python -m tools.daily_summary "
        f">> {LOG_PATH} 2>&1 {MARKER}"
    )


def status() -> CronStatus:
    """Inspect current crontab — return whether our entry is installed + its time.

    For multi-hour entries we surface the FIRST hour (mostly for the UI; full
    schedule is preserved in crontab itself). Settings UI shows the simple
    case; complex multi-time schedules are operator-edited via .env or this
    module's `install_multi`.
    """
    cron = _read_crontab()
    for line in cron.splitlines():
        if MARKER in line:
            m = re.match(r"^\s*(\d+)\s+([\d,]+)\s+", line)
            if m:
                first_hour = int(m.group(2).split(",")[0])
                return CronStatus(installed=True, hour=first_hour, minute=int(m.group(1)))
            return CronStatus(installed=True, hour=9, minute=13)
    return CronStatus(installed=False, hour=9, minute=13)


def install(hour: int, minute: int) -> CronStatus:
    """Install or update the daily-summary cron entry at a single hour.

    Use install_multi() for the production schedule (06/09/12/15/18/21).
    """
    if not (0 <= hour <= 23):
        raise ValueError(f"hour must be 0..23, got {hour}")
    if not (0 <= minute <= 59):
        raise ValueError(f"minute must be 0..59, got {minute}")
    cron = _strip_our_lines(_read_crontab())
    cron = (cron.rstrip("\n") + "\n" if cron else "") + _build_line(hour, minute) + "\n"
    _write_crontab(cron)
    return CronStatus(installed=True, hour=hour, minute=minute)


def install_multi(hours: list[int], minute: int = 0) -> CronStatus:
    """Install a multi-hour daily-summary schedule (one cron line, comma-list).

    Standard operator schedule: [6, 9, 12, 15, 18, 21] gives a report every
    3 hours during waking hours + the morning open. Single cron line keeps
    things tidy in `crontab -l`.
    """
    if not hours:
        raise ValueError("install_multi needs at least one hour")
    for h in hours:
        if not (0 <= h <= 23):
            raise ValueError(f"hour must be 0..23, got {h}")
    if not (0 <= minute <= 59):
        raise ValueError(f"minute must be 0..59, got {minute}")
    cron = _strip_our_lines(_read_crontab())
    cron = (cron.rstrip("\n") + "\n" if cron else "") + _build_line(hours, minute) + "\n"
    _write_crontab(cron)
    return CronStatus(installed=True, hour=sorted(hours)[0], minute=minute)


def uninstall() -> CronStatus:
    """Remove our entry (no-op if not installed)."""
    cron = _strip_our_lines(_read_crontab())
    _write_crontab(cron)
    return CronStatus(installed=False, hour=9, minute=13)
