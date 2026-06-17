"""Verdict colour is driven by AI adoption (share), with a read on output vs before."""
from __future__ import annotations

import pytest

from execution.products.ops.productivity.aggregate import _verdict


@pytest.mark.parametrize("kwargs, expected, reason_contains", [
    # no completed work in scope
    (dict(ai_share=None, throughput_vs_base=None, cycle_vs_base=None, has_activity=False),
     "NODATA", "no completed work"),
    (dict(ai_share=0.5, throughput_vs_base=10, cycle_vs_base=None, has_activity=False),
     "NODATA", "no completed work"),
    # heavy AI use -> green, and producing more than before
    (dict(ai_share=0.7, throughput_vs_base=50, cycle_vs_base=None, has_activity=True),
     "GREEN", "more than before"),
    # heavy AI use but producing less than before -> still green colour (high adoption),
    # reason flags the drop
    (dict(ai_share=0.6, throughput_vs_base=-40, cycle_vs_base=None, has_activity=True),
     "GREEN", "less than before"),
    # faster cycle counts as producing more
    (dict(ai_share=0.6, throughput_vs_base=None, cycle_vs_base=-30, has_activity=True),
     "GREEN", "more than before"),
    # partial adoption -> amber
    (dict(ai_share=0.3, throughput_vs_base=0, cycle_vs_base=None, has_activity=True),
     "AMBER", "partial use"),
    # low adoption -> red (they are not using the new system)
    (dict(ai_share=0.05, throughput_vs_base=20, cycle_vs_base=None, has_activity=True),
     "RED", "low use"),
])
def test_verdict_is_coloured_by_ai_share(kwargs, expected, reason_contains):
    verdict, reason = _verdict(**kwargs)
    assert verdict == expected
    assert reason_contains in reason.lower()
