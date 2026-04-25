"""Read / update specific keys in `.env`, preserving other lines, comments, blanks.

Used by the dashboard's Telegram setup wizard. Intentionally narrow — does not
support multiline values, escaping, or shell-style expansion. Just `KEY=value` lines.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_ENV_PATH = Path(".env")


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def read_env(path: Path = DEFAULT_ENV_PATH) -> dict[str, str]:
    """Parse `.env` into a flat `{KEY: value}` dict. Missing file → empty dict.

    Comments (`#...`), blank lines, and malformed lines (no `=`) are ignored.
    Surrounding single/double quotes on the value are stripped.
    """
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = _strip_quotes(value.strip())
    return out


def update_env(updates: dict[str, str], path: Path = DEFAULT_ENV_PATH) -> None:
    """Update `path` with the given keys.

    - Existing key (anywhere in the file): rewritten in place; surrounding lines preserved.
    - New key: appended at the end.
    - Other lines (comments, blanks, unrelated keys): preserved verbatim.

    The file is created if missing. Empty values write `KEY=` (intentional — caller
    can use this to "unset" something without deleting the line).
    """
    if not updates:
        return
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    pending = dict(updates)
    new_lines: list[str] = []
    for raw in path.read_text().splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(raw)
            continue
        key = stripped.partition("=")[0].strip()
        if key in pending:
            new_lines.append(f"{key}={pending.pop(key)}")
        else:
            new_lines.append(raw)

    for key, value in pending.items():
        new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n")
