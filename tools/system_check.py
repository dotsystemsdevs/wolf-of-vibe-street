"""macOS 24/7 readiness checks — `pmset` parsing + remediation hints.

Validates that the host Mac is configured to keep the live loop alive through
overnight runs, lid-close events, and the occasional power blip. Used by the
dashboard's Go-Live Readiness panel (#8) and is safe to call every render
(reads only; never writes settings).

Settings we care about for a 24/7 trading bot:
  sleep        = 0   System sleep off — Streamlit + live loop must stay running.
  disksleep    = 0   Disks always spun up — SQLite/Parquet writes are zero-latency.
  powernap     = 0   Off — prevents the system from waking + sleeping in cycles.
  autorestart  = 1   Reboot automatically after power loss — survives blackouts.
  womp         = 1   Wake on Magic Packet — let Tailscale/SSH revive a slept Mac.

We deliberately do NOT touch settings ourselves: `pmset` requires sudo, and any
silent change to system power policy is the kind of action that earns a "wait,
why is my Mac doing X" call from the operator. We surface status + a one-liner
the operator can paste into a terminal.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass

# Required state for each setting. value=None means "any non-empty value is OK"
# (kept here as future-proofing; today every requirement is an exact int).
REQUIRED_SETTINGS: dict[str, int] = {
    "sleep": 0,
    "disksleep": 0,
    "powernap": 0,
    "autorestart": 1,
    "womp": 1,
}

REMEDIATION_COMMAND = "sudo pmset -a sleep 0 disksleep 0 powernap 0 autorestart 1 womp 1"


@dataclass(frozen=True, slots=True)
class SettingCheck:
    name: str
    expected: int
    actual: int | None  # None = pmset didn't report this setting
    passed: bool


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    is_macos: bool
    pmset_available: bool
    raw_output: str | None
    checks: list[SettingCheck]
    remediation: str

    @property
    def is_clean(self) -> bool:
        """True only on macOS, with pmset output, and every required setting at expected value."""
        return self.is_macos and self.pmset_available and all(c.passed for c in self.checks)

    @property
    def summary(self) -> str:
        if not self.is_macos:
            return f"not macOS ({platform.system()}) — Mac-Mini-specific checks skipped"
        if not self.pmset_available:
            return "pmset not found in PATH — can't read power settings"
        if self.is_clean:
            return f"24/7 ready · all {len(self.checks)} settings correct"
        bad = [f"{c.name}={c.actual} (want {c.expected})" for c in self.checks if not c.passed]
        return f"NOT 24/7 ready · fix: {', '.join(bad)}"


def parse_pmset_output(text: str) -> dict[str, int]:
    """Parse `pmset -g` text into a {setting_name: int_value} dict.

    Single-word keys only — multi-word ones like "Sleep On Power Button" are
    deliberately skipped (we don't validate them, and they confuse the
    "first token = key" rule). Trailing notes in parens like
    "(sleep prevented by ...)" are ignored.
    """
    out: dict[str, int] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.endswith(":"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        # Single-word key requirement: parts[1] must itself parse as an int.
        # Lines like "Sleep On Power Button 1" fail this check (parts[1] = "On"),
        # so they're skipped — that's the behavior we want.
        try:
            value = int(parts[1])
        except ValueError:
            continue
        key = parts[0]
        if not key.isalpha():
            continue
        out[key] = value
    return out


def _run_pmset() -> tuple[bool, str | None]:
    """Returns (pmset_available, raw_output_or_none)."""
    if shutil.which("pmset") is None:
        return False, None
    try:
        result = subprocess.run(
            ["pmset", "-g"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return True, None
    if result.returncode != 0:
        return True, None
    return True, result.stdout


def check_readiness(*, fake_pmset_output: str | None = None) -> ReadinessReport:
    """Pull pmset settings (or use fake output for tests) and validate."""
    is_macos = platform.system() == "Darwin"
    if fake_pmset_output is not None:
        pmset_available = True
        raw = fake_pmset_output
    elif not is_macos:
        return ReadinessReport(
            is_macos=False,
            pmset_available=False,
            raw_output=None,
            checks=[],
            remediation=REMEDIATION_COMMAND,
        )
    else:
        pmset_available, raw = _run_pmset()

    if not pmset_available or raw is None:
        return ReadinessReport(
            is_macos=is_macos,
            pmset_available=pmset_available,
            raw_output=raw,
            checks=[],
            remediation=REMEDIATION_COMMAND,
        )

    parsed = parse_pmset_output(raw)
    checks: list[SettingCheck] = []
    for name, expected in REQUIRED_SETTINGS.items():
        actual = parsed.get(name)
        checks.append(
            SettingCheck(
                name=name,
                expected=expected,
                actual=actual,
                passed=actual == expected,
            )
        )
    return ReadinessReport(
        is_macos=is_macos,
        pmset_available=True,
        raw_output=raw,
        checks=checks,
        remediation=REMEDIATION_COMMAND,
    )
