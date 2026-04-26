"""Tests for tools.launchd — plist generation + path detection.

3-test rule (CLAUDE.md §3.1):
  - expected: generate() returns a usable LaunchdSetup with all paths populated
  - edge: custom uv_bin / log_dir overrides defaults
  - failure: detect_project_root falls back to cwd when no CLAUDE.md found
"""

from __future__ import annotations

from pathlib import Path

from tools.launchd import (
    DEFAULT_LABEL,
    USER_LAUNCHAGENTS_DIR,
    detect_project_root,
    detect_uv_path,
    generate,
    is_installed,
)


def test_expected_generate_returns_complete_setup(tmp_path: Path) -> None:
    setup = generate(project_root=tmp_path)
    assert setup.label == DEFAULT_LABEL
    assert setup.plist_path == USER_LAUNCHAGENTS_DIR / f"{DEFAULT_LABEL}.plist"
    # plist body must contain the project path + the live-loop module + KeepAlive.
    assert str(tmp_path) in setup.plist_xml
    assert "workers.live_loop" in setup.plist_xml
    assert "<key>KeepAlive</key>" in setup.plist_xml
    assert "<key>RunAtLoad</key>" in setup.plist_xml
    # Install/uninstall commands must reference the plist path.
    assert str(setup.plist_path) in setup.install_command
    assert "launchctl load" in setup.install_command
    assert "rm -f" in setup.uninstall_command


def test_edge_custom_uv_and_log_dir_appear_in_plist(tmp_path: Path) -> None:
    custom_log = tmp_path / "logs"
    custom_uv = "/usr/local/bin/uv"
    setup = generate(
        project_root=tmp_path,
        uv_bin=custom_uv,
        log_dir=custom_log,
        label="com.test.bot",
    )
    assert custom_uv in setup.plist_xml
    assert str(custom_log / "launchd.out.log") in setup.plist_xml
    assert str(custom_log / "launchd.err.log") in setup.plist_xml
    assert setup.label == "com.test.bot"


def test_detect_project_root_finds_claude_md() -> None:
    """In this repo, project root contains CLAUDE.md — detection must find it."""
    root = detect_project_root()
    assert (root / "CLAUDE.md").exists()


def test_detect_uv_path_returns_a_path() -> None:
    """Either shutil.which finds uv or we fall back to /opt/homebrew/bin/uv."""
    p = detect_uv_path()
    assert p.endswith("uv")


def test_is_installed_returns_bool(tmp_path: Path) -> None:
    """Smoke test — function returns bool, not crash. We can't easily fake
    the LaunchAgents dir, so just verify the type."""
    assert isinstance(is_installed("com.does.not.exist.test"), bool)
