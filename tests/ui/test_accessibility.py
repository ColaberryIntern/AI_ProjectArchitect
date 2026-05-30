"""Accessibility CI gate — Playwright + axe-core.

Skipped by default (requires running server + browser). Enable via:

    pytest tests/ui/test_accessibility.py --base-url=http://localhost:8765

or set env COLABERRY_A11Y_URL=http://localhost:8765 to opt-in.

Pages tested = every "shell" page across the three product surfaces.
Each page must produce zero axe-core 'critical' or 'serious' violations.
"""

from __future__ import annotations

import os

import pytest

try:
    from playwright.sync_api import sync_playwright

    _PW_AVAILABLE = True
except Exception:  # pragma: no cover
    _PW_AVAILABLE = False


BASE_URL = os.environ.get("COLABERRY_A11Y_URL")

pytestmark = pytest.mark.skipif(
    not BASE_URL or not _PW_AVAILABLE,
    reason="A11y suite is opt-in: set COLABERRY_A11Y_URL=http://localhost:<port>",
)


# ── Shells (one per product). Add more as they appear. ──────────
PRODUCT_PAGES: list[tuple[str, str]] = [
    # Architect product
    ("architect/landing", "/"),
    # Ops product (one representative page per cognitive mode)
    ("ops/home",       "/ops/"),
    ("ops/dashboard",  "/ops/dashboard"),
    ("ops/builder",    "/ops/builder"),
    ("ops/pipelines",  "/ops/pipelines"),
    ("ops/copilot",    "/ops/copilot/page"),
    ("ops/audit",      "/ops/audit/page"),
    # Library product (overview + one populated category)
    ("library/home",         "/library/"),
    ("library/skills",       "/library/skills"),
    ("library/agents",       "/library/agents"),
    ("library/capabilities", "/library/capabilities"),
    ("library/policies",     "/library/policies"),
    ("library/workflows",    "/library/workflows"),
    ("library/projections",  "/library/projections"),
    ("library/recovery",     "/library/recovery"),
]


AXE_CDN = "https://cdn.jsdelivr.net/npm/axe-core@4.10.0/axe.min.js"


def _run_axe(page) -> dict:
    """Inject axe-core and run a full audit."""
    page.add_script_tag(url=AXE_CDN)
    page.wait_for_function("typeof axe !== 'undefined'", timeout=5000)
    return page.evaluate("async () => await axe.run()")


@pytest.fixture(scope="module")
def browser_ctx():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        yield ctx
        browser.close()


@pytest.mark.parametrize(("label", "path"), PRODUCT_PAGES,
                                ids=[label for label, _ in PRODUCT_PAGES])
def test_page_has_no_serious_or_critical_a11y_violations(browser_ctx, label, path):
    page = browser_ctx.new_page()
    try:
        page.goto(BASE_URL + path, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(500)
        results = _run_axe(page)
        serious_or_critical = [
            v for v in results.get("violations", [])
            if v.get("impact") in ("critical", "serious")
        ]
        assert not serious_or_critical, (
            f"\n{label} ({path}) has {len(serious_or_critical)} "
            f"critical/serious a11y violation(s):\n"
            + "\n".join(
                f"  • {v['id']} ({v['impact']}): {v['help']}\n    nodes: {len(v.get('nodes', []))}"
                for v in serious_or_critical
            )
        )
    finally:
        page.close()


@pytest.mark.parametrize(("label", "path"), PRODUCT_PAGES,
                                ids=[label for label, _ in PRODUCT_PAGES])
def test_page_responds_with_2xx(browser_ctx, label, path):
    page = browser_ctx.new_page()
    try:
        resp = page.goto(BASE_URL + path, wait_until="domcontentloaded",
                                timeout=15000)
        assert resp is not None
        assert 200 <= resp.status < 300, (
            f"{label} ({path}) returned HTTP {resp.status}"
        )
    finally:
        page.close()
