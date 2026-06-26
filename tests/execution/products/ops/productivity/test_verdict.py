"""Verdict colour is driven by AI adoption (share) but GATED on attribution confidence:
a measurement gap (unattributed work) must read "attribution incomplete", never "Low AI use".
"""
from __future__ import annotations

import pytest

from execution.products.ops.productivity.aggregate import _verdict


@pytest.mark.parametrize("kwargs, expected, reason_contains", [
    # no completed work in scope
    (dict(ai_share=None, attribution_confidence=None, ai_active=False,
          throughput_vs_base=None, cycle_vs_base=None, has_activity=False),
     "NODATA", "no completed work"),
    # low attribution confidence -> NEVER "low use"; reads "attribution incomplete"
    (dict(ai_share=0.0, attribution_confidence=0.2, ai_active=False,
          throughput_vs_base=None, cycle_vs_base=None, has_activity=True),
     "UNKNOWN", "attribution incomplete"),
    # confidence unknown (None) but there is activity -> still UNKNOWN, not RED
    (dict(ai_share=0.0, attribution_confidence=None, ai_active=False,
          throughput_vs_base=None, cycle_vs_base=None, has_activity=True),
     "UNKNOWN", "attribution incomplete"),
    # AI-active operator with thin attribution -> explicitly "not a low-use call"
    (dict(ai_share=1.0, attribution_confidence=0.3, ai_active=True,
          throughput_vs_base=None, cycle_vs_base=None, has_activity=True),
     "UNKNOWN", "not a low-use"),
    # confident + heavy AI share -> green, producing more than before
    (dict(ai_share=0.7, attribution_confidence=1.0, ai_active=True,
          throughput_vs_base=50, cycle_vs_base=None, has_activity=True),
     "GREEN", "more than before"),
    # confident + heavy AI share but producing less than before -> still green colour
    (dict(ai_share=0.6, attribution_confidence=0.9, ai_active=True,
          throughput_vs_base=-40, cycle_vs_base=None, has_activity=True),
     "GREEN", "less than before"),
    # the paradox case: faster cycle counts as producing more (cycle down + throughput flat)
    (dict(ai_share=0.6, attribution_confidence=0.9, ai_active=True,
          throughput_vs_base=0, cycle_vs_base=-30, has_activity=True),
     "GREEN", "more than before"),
    # confident + partial adoption -> amber
    (dict(ai_share=0.3, attribution_confidence=1.0, ai_active=True,
          throughput_vs_base=0, cycle_vs_base=None, has_activity=True),
     "AMBER", "partial use"),
    # confident AND genuinely low attributed AI share -> RED (real human-only work dominates)
    (dict(ai_share=0.05, attribution_confidence=1.0, ai_active=False,
          throughput_vs_base=20, cycle_vs_base=None, has_activity=True),
     "RED", "low use"),
])
def test_verdict_gated_on_attribution_confidence(kwargs, expected, reason_contains):
    verdict, reason = _verdict(**kwargs)
    assert verdict == expected
    assert reason_contains in reason.lower()


def test_low_confidence_never_says_low_use():
    # The exact bug: thin attribution must not produce the "Low AI use" verdict.
    verdict, reason = _verdict(ai_share=0.0, attribution_confidence=0.1, ai_active=True,
                               throughput_vs_base=None, cycle_vs_base=None, has_activity=True)
    assert verdict == "UNKNOWN"
    assert "low ai use" not in reason.lower()
