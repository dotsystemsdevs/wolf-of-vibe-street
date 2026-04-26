"""Generate a launchd plist to auto-start the live loop on macOS reboot.

Why this exists: a 30-day live run on a Mac Mini will see at least one reboot
(power blip, OS update, kernel panic, the cat steps on the keyboard). pmset
autorestart=1 brings the Mac back up; this brings the trading loop back up
with it. Without it, the bot is silently down between the reboot and the
operator manually clicking Start in the dashboard.

Pure plist generation — we never call `launchctl load` ourselves. Output is
a string the operator pastes into a file + a one-liner load command. Same
philosophy as tools/system_check.py: surface the change, never make it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LABEL = "com.dotsystemsdevs.wolfofvibestreet"
# launchd looks here for per-user agents. Created on first install.
USER_LAUNCHAGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


@dataclass(frozen=True, slots=True)
class LaunchdSetup:
    label: str
    plist_path: Path
    plist_xml: str
    install_command: str
    uninstall_command: str
    log_path: Path
    err_log_path: Path


def generate(
    *,
    project_root: Path,
    uv_bin: str | None = None,
    label: str = DEFAULT_LABEL,
    log_dir: Path | None = None,
) -> LaunchdSetup:
    """Build the LaunchAgent plist + install/uninstall commands.

    project_root: where to chdir before running the loop (so .env + data/ resolve).
    uv_bin: full path to the `uv` binary; defaults to /opt/homebrew/bin/uv (Homebrew
      on Apple Silicon). Override if your `which uv` says different.
    label: launchd identifier. Stays as DEFAULT_LABEL unless you're running
      multiple bots side by side.
    log_dir: where stdout/stderr go. Defaults to <project_root>/data/state/.
    """
    plist_path = USER_LAUNCHAGENTS_DIR / f"{label}.plist"
    uv = uv_bin or "/opt/homebrew/bin/uv"
    logs = log_dir or (project_root / "data" / "state")
    out_log = logs / "launchd.out.log"
    err_log = logs / "launchd.err.log"

    plist_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{uv}</string>
        <string>run</string>
        <string>python</string>
        <string>-m</string>
        <string>workers.live_loop</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{project_root}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>{out_log}</string>

    <key>StandardErrorPath</key>
    <string>{err_log}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""
    install_command = (
        f"mkdir -p {USER_LAUNCHAGENTS_DIR} && "
        f"launchctl unload {plist_path} 2>/dev/null; "
        f"launchctl load -w {plist_path}"
    )
    uninstall_command = f"launchctl unload -w {plist_path} 2>/dev/null; rm -f {plist_path}"
    return LaunchdSetup(
        label=label,
        plist_path=plist_path,
        plist_xml=plist_xml,
        install_command=install_command,
        uninstall_command=uninstall_command,
        log_path=out_log,
        err_log_path=err_log,
    )


def is_installed(label: str = DEFAULT_LABEL) -> bool:
    """True if the plist exists in the user's LaunchAgents dir."""
    return (USER_LAUNCHAGENTS_DIR / f"{label}.plist").exists()


def detect_uv_path() -> str:
    """Best-effort lookup for the user's uv binary. Falls back to Homebrew default."""
    import shutil  # noqa: PLC0415

    found = shutil.which("uv")
    if found:
        return found
    # Homebrew default on Apple Silicon. If the operator has Intel Brew it's
    # /usr/local/bin/uv — we surface this in the UI so they can edit if needed.
    return "/opt/homebrew/bin/uv"


def detect_project_root() -> Path:
    """Walk up from this file to find the project root (the dir containing CLAUDE.md)."""
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "CLAUDE.md").exists():
            return candidate
    # Fallback: cwd. Works as long as the operator is in the project dir.
    return Path(os.getcwd())
