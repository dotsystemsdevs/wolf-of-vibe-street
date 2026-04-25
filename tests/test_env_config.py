"""Tests for tools.env_config — read + update preserving structure."""

from __future__ import annotations

from pathlib import Path

from tools.env_config import read_env, update_env


def test_read_env_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_env(tmp_path / "nope.env") == {}


def test_read_env_parses_basic_kv(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FOO=bar\nBAZ=qux\n")
    assert read_env(env) == {"FOO": "bar", "BAZ": "qux"}


def test_read_env_strips_quotes(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("TOKEN=\"abc123\"\nNAME='alice'\nNAKED=plain\n")
    assert read_env(env) == {"TOKEN": "abc123", "NAME": "alice", "NAKED": "plain"}


def test_read_env_ignores_comments_and_blanks(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# header comment\n\nFOO=bar\n# inline-section\nBAZ=qux\n")
    assert read_env(env) == {"FOO": "bar", "BAZ": "qux"}


def test_update_env_creates_file_if_missing(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    update_env({"FOO": "bar"}, env)
    assert read_env(env) == {"FOO": "bar"}


def test_update_env_replaces_existing_key_in_place(tmp_path: Path) -> None:
    """The line position must be preserved — only the value changes."""
    env = tmp_path / ".env"
    env.write_text("FIRST=1\nTOKEN=old\nLAST=last\n")
    update_env({"TOKEN": "new"}, env)
    text = env.read_text()
    lines = text.splitlines()
    assert lines[0] == "FIRST=1"
    assert lines[1] == "TOKEN=new"
    assert lines[2] == "LAST=last"


def test_update_env_appends_new_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("EXISTING=1\n")
    update_env({"NEW_KEY": "value"}, env)
    text = env.read_text()
    assert "EXISTING=1" in text
    assert "NEW_KEY=value" in text


def test_update_env_preserves_comments_and_blanks(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# header\n\nFOO=bar\n# section\nBAZ=qux\n")
    update_env({"FOO": "newbar"}, env)
    text = env.read_text()
    assert "# header" in text
    assert "# section" in text
    assert "FOO=newbar" in text
    assert "BAZ=qux" in text


def test_update_env_empty_dict_is_noop(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("KEY=value\n")
    original = env.read_text()
    update_env({}, env)
    assert env.read_text() == original


def test_update_env_unrelated_keys_untouched(tmp_path: Path) -> None:
    """Setting TELEGRAM_BOT_TOKEN must not lose ANTHROPIC_API_KEY."""
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-secret\nLIVE_TRADING=false\n")
    update_env({"TELEGRAM_BOT_TOKEN": "tok"}, env)
    parsed = read_env(env)
    assert parsed["ANTHROPIC_API_KEY"] == "sk-secret"
    assert parsed["LIVE_TRADING"] == "false"
    assert parsed["TELEGRAM_BOT_TOKEN"] == "tok"
