"""_clean_hostname rejects unexpanded shell templates.

Real bug (2026-06-14 audit): a Windows operator pasted the cmd.exe setup
command (`X-MCP-Hostname: %COMPUTERNAME%`) into PowerShell, where the
variable doesn't expand, so the device registered with the literal
hostname "%COMPUTERNAME%". The guard treats any wrong-shell template as
"no hostname reported" instead of storing garbage.
"""
from __future__ import annotations

import pytest

from execution.products.library.mcp_token import _clean_hostname


@pytest.mark.parametrize("bad", [
    "%COMPUTERNAME%",        # cmd snippet pasted into PowerShell
    "$env:COMPUTERNAME",     # PowerShell snippet pasted into cmd
    "$(hostname)",           # Unix snippet, unexpanded
    "$COMPUTERNAME",
    "`hostname`",
    "  %COMPUTERNAME%  ",    # whitespace-padded
    "",
    "   ",
    None,
])
def test_rejects_templates_and_blanks(bad):
    assert _clean_hostname(bad) is None


@pytest.mark.parametrize("good,expected", [
    ("DESKTOP-7L8UA4M", "DESKTOP-7L8UA4M"),
    ("Kesetebirhan", "Kesetebirhan"),
    ("colaberry016s-MacBook-Air.local", "colaberry016s-MacBook-Air.local"),
    ("CLB-007", "CLB-007"),
    ("  ALI-AI  ", "ALI-AI"),          # trimmed
])
def test_keeps_real_hostnames(good, expected):
    assert _clean_hostname(good) == expected


def test_truncates_to_120():
    assert len(_clean_hostname("h" * 200)) == 120
