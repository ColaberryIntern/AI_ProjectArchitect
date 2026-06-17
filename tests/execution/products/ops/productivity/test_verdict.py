"""The assessment logic — GREEN / AMBER / RED / BASELINE, including the
productivity-paradox case (faster per task but NOT completing more)."""
from __future__ import annotations

import pytest

from execution.products.ops.productivity.aggregate import _verdict


@pytest.mark.parametrize("kwargs, expected, reason_contains", [
    # too little post-launch data -> baseline building
    (dict(completed_7d=1, completed_prior_7d=1, base_weekly=2.0,
          cycle_vs_baseline_pct=-20, overdue_rate=0.0), "BASELINE", "baseline"),
    # no pre-launch baseline to compare against
    (dict(completed_7d=5, completed_prior_7d=5, base_weekly=None,
          cycle_vs_baseline_pct=-20, overdue_rate=0.0), "BASELINE", "baseline"),
    # quality gate: high overdue share -> speed costing quality
    (dict(completed_7d=5, completed_prior_7d=5, base_weekly=2.0,
          cycle_vs_baseline_pct=-20, overdue_rate=0.5), "RED", "overdue"),
    # genuinely more productive: more output, not slower
    (dict(completed_7d=5, completed_prior_7d=4, base_weekly=2.0,
          cycle_vs_baseline_pct=-20, overdue_rate=0.1), "GREEN", "more productive"),
    # THE paradox: faster per task but not completing more
    (dict(completed_7d=2, completed_prior_7d=3, base_weekly=2.0,
          cycle_vs_baseline_pct=-30, overdue_rate=0.1), "AMBER", "faster per task"),
    # slower AND completing less -> red
    (dict(completed_7d=1, completed_prior_7d=2, base_weekly=2.0,
          cycle_vs_baseline_pct=30, overdue_rate=0.1), "RED", "slower"),
    # flat throughput, flat cycle -> mixed amber
    (dict(completed_7d=2, completed_prior_7d=2, base_weekly=2.0,
          cycle_vs_baseline_pct=0, overdue_rate=0.1), "AMBER", "mixed"),
])
def test_verdict_cases(kwargs, expected, reason_contains):
    verdict, reason = _verdict(**kwargs)
    assert verdict == expected
    assert reason_contains in reason.lower()
