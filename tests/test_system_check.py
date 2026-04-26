"""Tests for tools.system_check — pmset parsing + readiness rollup.

3-test rule per CLAUDE.md §3.1:
  - expected: a fully-correct pmset output yields all-pass
  - edge: pmset output with extras/multi-word keys parses cleanly
  - failure: missing/wrong settings flagged with actionable summary
"""

from __future__ import annotations

from tools.system_check import (
    REMEDIATION_COMMAND,
    REQUIRED_SETTINGS,
    check_readiness,
    parse_pmset_output,
)

PASSING_OUTPUT = """\
System-wide power settings:
Currently in use:
 sleep                0
 disksleep            0
 powernap             0
 autorestart          1
 womp                 1
 displaysleep         5
 standby              0
"""

REAL_MAC_OUTPUT = """\
System-wide power settings:
Currently in use:
 disksleep            10
 powernap             1
 womp                 1
 networkoversleep     0
 sleep                0 (sleep prevented by powerd, caffeinate)
 Sleep On Power Button 1
 ttyskeepawake        1
 tcpkeepalive         1
 autorestart          0
 standby              0
 displaysleep         0 (display sleep prevented by caffeinate)
"""


def test_expected_clean_output_all_settings_pass() -> None:
    report = check_readiness(fake_pmset_output=PASSING_OUTPUT)
    assert report.is_clean
    assert len(report.checks) == len(REQUIRED_SETTINGS)
    assert all(c.passed for c in report.checks)
    assert "ready" in report.summary


def test_edge_real_mac_output_parses_and_flags_failures() -> None:
    """Real pmset on a not-yet-configured Mac: disksleep=10, powernap=1, autorestart=0 fail."""
    report = check_readiness(fake_pmset_output=REAL_MAC_OUTPUT)
    assert not report.is_clean
    failed = {c.name: c for c in report.checks if not c.passed}
    assert "disksleep" in failed and failed["disksleep"].actual == 10
    assert "powernap" in failed and failed["powernap"].actual == 1
    assert "autorestart" in failed and failed["autorestart"].actual == 0
    # Settings that ARE correct should still pass:
    passing = {c.name for c in report.checks if c.passed}
    assert "sleep" in passing  # 0 thanks to caffeinate
    assert "womp" in passing  # 1 already
    # Multi-word "Sleep On Power Button" must NOT pollute the parsed dict.
    parsed = parse_pmset_output(REAL_MAC_OUTPUT)
    assert "Sleep" not in parsed  # multi-word key skipped
    assert parsed["sleep"] == 0


def test_failure_remediation_contains_all_required_keys() -> None:
    """The copy-paste fix must mention every required setting."""
    for name in REQUIRED_SETTINGS:
        assert name in REMEDIATION_COMMAND, f"missing {name} in remediation: {REMEDIATION_COMMAND}"


def test_parse_pmset_handles_inline_parens() -> None:
    """`sleep 0 (sleep prevented by ...)` must parse value 0, not crash."""
    parsed = parse_pmset_output(" sleep                0 (sleep prevented by powerd)")
    assert parsed.get("sleep") == 0


def test_parse_pmset_skips_unparseable_lines() -> None:
    """Header rows + free-form text must be silently dropped."""
    text = """
System-wide power settings:
Currently in use:
 sleep                0
some random non-setting line
"""
    parsed = parse_pmset_output(text)
    assert parsed == {"sleep": 0}
