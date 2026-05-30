"""Capture live UI screenshots for every ops/* page + a few Project Architect
pages. Requires server running on http://localhost:8765.

Run:  python scripts/capture_real_ui.py
Output: output/system_tour/screenshots/live_*.png
        output/system_tour/live_pages.json  (list of {label, file, url, status})
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "output" / "system_tour" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
INDEX = ROOT / "output" / "system_tour" / "live_pages.json"

BASE = "http://localhost:8765"


# (label, slug used in screenshot filename, URL path)
PAGES: list[tuple[str, str, str]] = [
    # Phase 0 — Project Architect landing
    ("📐 Project list (landing)",      "p0_landing",        "/"),

    # Phase 1-9 ops UI
    ("🏠 Ops Home",                     "ops_home",          "/ops/"),
    ("📊 Ops Dashboard",                "ops_dashboard",     "/ops/dashboard"),
    ("👥 Workspaces",                   "ops_workspaces",    "/ops/workspaces/page"),
    ("🧠 Discovery Queue",              "ops_discovery",     "/ops/discovery-queue"),
    ("💡 Recommendations",              "ops_recommend",     "/ops/recommend"),
    ("⚙️ Optimizer",                    "ops_optimizer",     "/ops/optimizer"),
    ("🛠️ Builder",                      "ops_builder",       "/ops/builder"),
    ("🎼 Pipelines",                    "ops_pipelines",     "/ops/pipelines"),
    ("🤖 Copilot",                      "ops_copilot",       "/ops/copilot/page"),
    ("🧑‍💼 Execution Assistant",         "ops_assistant",     "/ops/assistant"),
    ("📈 Analytics",                    "ops_analytics",     "/ops/analytics"),
    ("👔 Executive Report",             "ops_executive",     "/ops/reporting/executive/page"),
    ("🔐 Audit Log",                    "ops_audit",         "/ops/audit/page"),
    ("🔁 Replay",                       "ops_replay",        "/ops/replay/page"),

    # Phase 10 — JSON-only endpoints, still capture
    ("🩺 Cluster Health (JSON)",        "ops_health",        "/ops/system/cluster-health"),
    ("📤 Outbox Metrics (JSON)",        "ops_outbox",        "/ops/outbox/metrics"),
    ("🌐 Coordination Topology (JSON)", "ops_topology",      "/ops/coordination/topology"),
]


def capture():
    captured: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})

        for label, slug, path in PAGES:
            url = BASE + path
            fname = f"live_{slug}.png"
            target = SHOTS / fname
            page = ctx.new_page()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                status = resp.status if resp else 0
                page.wait_for_timeout(800)
                page.screenshot(path=str(target), full_page=False)
                print(f"  ✅ {status} {label:32s} {url}")
                captured.append({"label": label, "file": fname, "url": url,
                                       "status": status, "ok": 200 <= status < 400})
            except Exception as e:
                print(f"  ❌ ERR  {label:32s} {url} ({type(e).__name__}: {e})")
                captured.append({"label": label, "file": fname, "url": url,
                                       "status": 0, "ok": False,
                                       "error": f"{type(e).__name__}: {e}"})
            finally:
                page.close()

        browser.close()

    INDEX.write_text(json.dumps(captured, indent=2), encoding="utf-8")
    ok = sum(1 for c in captured if c.get("ok"))
    print(f"\n📊 Captured {ok}/{len(captured)} OK. Index: {INDEX}")


if __name__ == "__main__":
    capture()
