"""Library v5 — Use Cases lead, LLM-generated, cross-linked to tools.

Captures the use-cases-first redesign, the LLM generator + scheduler,
the cross-reference panel, and the actionable links layer.
"""

from __future__ import annotations

import html
import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "system_tour" / "library_v5.html"


PAGES = [
    {"title": "🏠 Home — leads with real-world use cases",
       "shot": "v7_lib_home.png",
       "url": "/library/?ws=global",
       "what": "Hero count: '62 use cases · 514 assets'. '🎯 Real-world use cases' card immediately below the hero with 6 persona-driven cards: title · complexity badge · industry · named persona · pain summary · outcome metric in green · tool count · rating."},
    {"title": "🎯 Use Cases index — sortable, filterable",
       "shot": "v7_use_cases.png",
       "url": "/library/use-cases?ws=global",
       "what": "62 cards. Sort: Newest / Top rated / By persona. Filter: ✓ Colaberry vetted only. 'Generate one now' button. Each card shows complexity + industry + persona + tools."},
    {"title": "📄 Use case detail — Maya the Proposal Manager",
       "shot": "v7_uc_detail.png",
       "url": "/library/use-cases/<id>?ws=global",
       "what": "Named persona ('Maya, a Proposal Manager at a 150-person B2B SaaS'). 🔥 Problem · 💡 Solution · 🚶 Walkthrough · 📊 Outcome. Right column: 🧩 Tools used (linked + 'role' per tool) · ⭐ Rate · 📁 Provenance."},
    {"title": "🔌 MCP Filesystem Server — actionable detail page",
       "shot": "v7_asset_filesys.png",
       "url": "/library/mcp/MCP%20Filesystem%20Server?ws=global",
       "what": "Action row right under tags: '🌐 View source' — and other action buttons (📋 Copy install / ⬇️ Install page / 📖 Docs / 🏠 Homepage) appear when enrichment extracts them. '🎯 Used in N use cases' panel on the right."},
    {"title": "💬 Asset with 32 linked use cases (the cross-ref payoff)",
       "shot": "v7_asset_with_crossrefs.png",
       "url": "/library/prompts/Claude%20Prompt%20Caching?ws=global",
       "what": "Claude Prompt Caching appears in 32 use cases. Right panel shows 5 with persona + complexity + industry, plus '+ 27 more'. Every tool in the library is now embedded in business context."},
]


GENERATOR_FLOW = [
    ("1. Sample tools",  "🎲", "_pick_tool_sample()",
     "Picks 3–5 assets across categories, preferring enriched ones (richer context for the LLM to compose from)."),
    ("2. Compose prompt", "✍️", "_SYSTEM_PROMPT + tools",
     "Strong 'be crafty' system prompt: insists on NAMED personas ('Maya, a Demand Gen Manager at a 50-person Series B SaaS'), specific numbers, 2-4 tools, 5-step walkthrough, quantifiable outcome."),
    ("3. LLM call",      "🧠", "llm_client.chat(json_object)",
     "GPT response_format=json_object returns strict JSON. Temperature 0.85 for variety."),
    ("4. Validate",      "✅", "_validate(raw)",
     "Required fields: title, persona, problem, solution, walkthrough ≥3 steps, tools_used non-empty. Fails → fall back to crafted bank."),
    ("5. Persist",       "💾", "use_cases.save(uc)",
     "Auto-vetted on creation (source='llm-generated' or 'hand-crafted'). Generator metadata recorded."),
]


SCHEDULER = [
    ("📅 Bootstrap",       "On startup, if count < LIBRARY_UC_BOOTSTRAP_COUNT (default 50), fills the gap. Idempotent — skips when over threshold."),
    ("🌙 Daily",           "Cron: every day at LIBRARY_UC_DAILY_HOUR:MINUTE UTC (default 03:00). Generates LIBRARY_UC_DAILY_COUNT (default 2)."),
    ("🔧 Config",          "All knobs are env vars — no code change to dial up/down or change time zone."),
    ("🛡️ Degradation",     "If APScheduler not installed → logs and continues. If LLM unavailable → hand-crafted bank fallback. App never fails to start."),
]


FILES = [
    ("📦 Use Case data model",        "execution/products/library/use_cases.py",
     "UseCase dataclass (title/persona/problem/solution/walkthrough/tools_used/outcome_metric/...). save/get/list/find_by_tool. Ratings + comments."),
    ("🧠 Use Case generator",         "execution/products/library/use_case_generator.py",
     "LLM-driven via OpenAI client (json_object response). 6 hand-crafted fallback templates. Validates required fields."),
    ("⏰ Use Case scheduler",          "execution/products/library/use_case_scheduler.py",
     "APScheduler wiring. bootstrap + daily jobs. Env-driven config."),
    ("🌐 Use Case router",            "app/routers/library.py",
     "/library/use-cases (index), /library/use-cases/{id} (detail with hydrated tools), /generate (manual trigger), /rate, /comment."),
    ("📋 Use Cases templates",        "app/templates/library/use_cases.html + use_case_detail.html",
     "Card grid index + rich detail page (persona, problem, solution, walkthrough, outcome, tools used, ratings, comments)."),
    ("🏠 Home redesign",              "app/templates/library/home.html",
     "Leads with '🎯 Real-world use cases' card (top 6 by rating). Featured-of-the-day moved below. Category browse below that."),
    ("🎯 Sidebar — Use Cases first",   "app/templates/library/_library_base.html",
     "🎯 Use Cases pinned as first nav item with count badge. Top-bar nav unchanged."),
    ("📄 Asset page — action row + cross-refs", "app/templates/library/asset.html",
     "Prominent '🎯 What it's used for' card. Action row (View source / Copy install / Install page / Docs / Homepage). '🎯 Used in N use cases' panel — solves 'empty messaging'."),
    ("🧩 Enricher v2 — actionable layer", "execution/products/library/enrichment_job.py",
     "Extracts install_command (npm/yarn/pnpm/pip/uv/cargo/brew patterns), install_url (npm/PyPI), docs_url, homepage_url, what_its_for (3-tier fallback)."),
    ("📚 AssetMetadata extended",     "execution/products/library/store.py",
     "Added: what_its_for, install_command, install_url, docs_url, homepage_url. Backward-compatible loader (drops unknown keys)."),
    ("🔌 App lifespan wired",          "app/main.py",
     "Use case scheduler joins skill scanner scheduler in the lifespan. Both gracefully no-op if APScheduler missing."),
]


def loc_size(path: str) -> str:
    p = ROOT / path.split(" + ")[0]
    if not p.exists() or not p.is_file():
        return "—"
    n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
    return f"{n} LOC · {p.stat().st_size/1024:.1f} KB"


def critique_form(key: str, label: str) -> str:
    safe = html.escape(label)
    return dedent(f"""
    <div class="bg-emerald-50 p-4 border-t border-slate-200" style="border-left:4px solid #1a7f37;">
      <div class="text-sm font-semibold mb-2 text-slate-800">💬 {safe}</div>
      <div class="flex flex-wrap gap-3 mb-2 text-sm">
        <label class="flex items-center gap-1"><input type="radio" name="verdict-{key}" value="approved" class="text-emerald-600"> ✅ Approved</label>
        <label class="flex items-center gap-1"><input type="radio" name="verdict-{key}" value="approved-with-notes" class="text-amber-600"> 🟡 With notes</label>
        <label class="flex items-center gap-1"><input type="radio" name="verdict-{key}" value="needs-work" class="text-rose-600"> 🔴 Needs work</label>
      </div>
      <textarea name="notes-{key}" rows="2"
                placeholder="Notes..."
                class="w-full border border-slate-300 rounded p-2 text-xs focus:outline-none focus:ring-2 focus:ring-emerald-400"></textarea>
    </div>""")


def build():
    pages_html = "".join(
        dedent(f"""
        <section class="bg-white rounded-2xl shadow-md border border-slate-200 mb-6 overflow-hidden">
          <div class="p-5 border-b border-slate-200">
            <h3 class="text-xl font-bold">{html.escape(p["title"])}</h3>
            <div class="text-xs text-slate-500 mt-1 font-mono">
              localhost:8765{html.escape(p["url"])}
            </div>
            <p class="text-sm text-slate-600 mt-2"><strong>👀 What to look for:</strong> {html.escape(p["what"])}</p>
          </div>
          <img src="screenshots/{p["shot"]}" class="w-full block">
          {critique_form('page-' + p["shot"].replace(".png",""), p["title"])}
        </section>""")
        for p in PAGES
    )

    flow_html = "".join(
        dedent(f"""
        <div class="bg-white border border-slate-200 rounded-xl p-4 mb-2 flex items-start gap-4">
          <div class="text-3xl flex-none">{emoji}</div>
          <div class="flex-1">
            <div class="font-bold text-sm">{html.escape(name)}</div>
            <div class="text-xs text-slate-500 font-mono">{html.escape(code)}</div>
            <p class="text-sm text-slate-700 mt-1">{html.escape(desc)}</p>
          </div>
        </div>""")
        for name, emoji, code, desc in GENERATOR_FLOW
    )

    scheduler_html = "".join(
        dedent(f"""
        <li class="mb-2"><strong>{html.escape(label)}</strong>: {html.escape(desc)}</li>
        """)
        for label, desc in SCHEDULER
    )

    files_html = "".join(
        dedent(f"""
        <tr class="border-b border-slate-100">
          <td class="py-3 pr-4 align-top">
            <div class="font-semibold text-sm">{html.escape(emoji_title)}</div>
            <div class="text-xs text-slate-500 font-mono mt-1">{html.escape(path)}</div>
          </td>
          <td class="py-3 pr-4 align-top text-xs text-slate-600 w-32">{loc_size(path)}</td>
          <td class="py-3 align-top text-sm text-slate-700">{html.escape(desc)}</td>
        </tr>""")
        for emoji_title, path, desc in FILES
    )

    return dedent(f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>🎯 Library v5 — Use Cases lead</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}</style>
</head><body class="bg-slate-50">

<div class="bg-gradient-to-br from-emerald-700 via-emerald-800 to-slate-900 text-white py-14">
  <div class="max-w-5xl mx-auto px-6 text-center">
    <div class="text-6xl mb-3">🎯 ➜ 📚</div>
    <h1 class="text-5xl font-extrabold leading-tight">Use cases lead.<br>Tools follow.</h1>
    <p class="text-xl text-slate-300 mt-4">
      62 LLM-crafted use cases with named personas + outcome metrics.<br>
      Cron job adds 2 every night. Every asset shows the use cases it powers.
    </p>
  </div>
</div>

<div class="max-w-6xl mx-auto px-6 py-10">

  <h2 class="text-3xl font-bold mb-3">🎯 What changed</h2>
  <div class="grid md:grid-cols-3 gap-5 mb-8">
    <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
      <div class="text-4xl mb-2">🎯</div>
      <div class="font-bold text-lg">Lead with use cases</div>
      <p class="text-sm text-slate-600 mt-2">🎯 Use Cases is now the first sidebar item. Home page hero shows real-world cases above the category tiles. Featured of the day moved below.</p>
    </div>
    <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
      <div class="text-4xl mb-2">🌙</div>
      <div class="font-bold text-lg">Cron-driven generation</div>
      <p class="text-sm text-slate-600 mt-2">Bootstrap 50 on first start. Daily 2 at 03:00 UTC. APScheduler-wired into app lifespan. Env-driven config. Graceful no-op when APScheduler not installed.</p>
    </div>
    <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
      <div class="text-4xl mb-2">🔗</div>
      <div class="font-bold text-lg">Cross-refs both ways</div>
      <p class="text-sm text-slate-600 mt-2">Use case → tools used (linked detail cards). Tool → 'Used in N use cases' panel. No more empty messaging — every asset shows the business context that makes it matter.</p>
    </div>
  </div>

  <!-- Generator -->
  <h2 class="text-3xl font-bold mt-12 mb-3">🧠 Generation pipeline (LLM + crafted fallback)</h2>
  <p class="text-slate-600 mb-4">The system prompt insists on craftiness — named personas, specific numbers, 2–4 tools, 5-step walkthrough, quantifiable outcome. Falls back to a 6-template hand-crafted bank if LLM is unavailable.</p>
  {flow_html}

  <h2 class="text-3xl font-bold mt-10 mb-3">⏰ Cron schedule</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
    <ul class="text-slate-700 space-y-1 list-disc ml-5">{scheduler_html}</ul>
  </div>

  <!-- Pages -->
  <h2 class="text-3xl font-bold mt-12 mb-3">🖼️ Page walkthrough</h2>
  {pages_html}

  <!-- Files -->
  <h2 class="text-3xl font-bold mt-12 mb-3">📦 Files this round</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden mb-6">
    <table class="w-full">
      <thead>
        <tr class="border-b-2 border-slate-200">
          <th class="text-left py-3 pr-4 text-xs uppercase text-slate-500 pl-5">File</th>
          <th class="text-left py-3 pr-4 text-xs uppercase text-slate-500">Size</th>
          <th class="text-left py-3 text-xs uppercase text-slate-500">What it does</th>
        </tr>
      </thead>
      <tbody>{files_html}</tbody>
    </table>
  </div>

  <!-- Try it -->
  <h2 class="text-3xl font-bold mt-12 mb-3">🚀 Try it now</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
    <ul class="text-slate-700 space-y-2 list-disc ml-5">
      <li>🎯 <a href="http://localhost:8765/library/use-cases?ws=global" target="_blank" class="text-emerald-700 font-mono hover:underline">/library/use-cases</a> — see the 62 generated use cases</li>
      <li>🏠 <a href="http://localhost:8765/library/?ws=global" target="_blank" class="text-emerald-700 font-mono hover:underline">/library/</a> — home now leads with use cases</li>
      <li>🔌 <a href="http://localhost:8765/library/prompts/Claude%20Prompt%20Caching?ws=global" target="_blank" class="text-emerald-700 font-mono hover:underline">Claude Prompt Caching</a> — asset detail showing "Used in 32 use cases"</li>
      <li>➕ Click "+ Generate one now" on the use cases page → makes one live</li>
      <li>🌙 Override schedule for testing: <code>LIBRARY_UC_DAILY_HOUR=14 LIBRARY_UC_DAILY_MINUTE=0</code></li>
    </ul>
  </div>

  <h2 id="critique" class="text-3xl font-bold mt-12 mb-3">🎯 Overall verdict</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
    <textarea id="overall-notes" rows="6"
              placeholder="Anything off? What's next?"
              class="w-full border border-slate-300 rounded-md p-3 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"></textarea>
    <div class="mt-5 flex items-center gap-3">
      <button onclick="generateResponse()" class="bg-emerald-600 hover:bg-emerald-700 text-white font-semibold px-6 py-3 rounded-lg shadow-md flex items-center gap-2 text-lg">
        <span>🎯</span> Generate Response for Claude
      </button>
    </div>
  </div>

</div>

<div id="response-modal" class="fixed inset-0 bg-black/60 z-50 items-center justify-center p-6" style="display:none;">
  <div class="bg-white rounded-2xl shadow-2xl max-w-4xl w-full max-h-[85vh] flex flex-col">
    <div class="p-5 border-b border-slate-200 flex items-center justify-between">
      <h3 class="text-xl font-bold flex items-center gap-2"><span>📨</span>Paste back to Claude</h3>
      <button onclick="closeModal()" class="text-slate-400 hover:text-slate-700 text-2xl leading-none">×</button>
    </div>
    <div class="p-5 flex-1 overflow-hidden flex flex-col">
      <textarea id="response-output" readonly class="flex-1 border border-slate-300 rounded-lg p-4 font-mono text-sm bg-slate-50 resize-none" style="min-height:300px;"></textarea>
      <div class="mt-4 flex gap-3">
        <button id="copy-btn" onclick="copyResponse()" class="bg-emerald-600 hover:bg-emerald-700 text-white font-semibold px-5 py-2 rounded-lg flex items-center gap-2"><span>📋</span><span>Copy</span></button>
        <button onclick="closeModal()" class="bg-slate-200 hover:bg-slate-300 px-5 py-2 rounded-lg">Close</button>
      </div>
    </div>
  </div>
</div>

<script>
const KEYS = {json.dumps([("page-" + p["shot"].replace(".png",""), p["title"]) for p in PAGES])};
function collectSection(key, label) {{
  const v = document.querySelector('input[name="verdict-' + key + '"]:checked');
  const n = document.querySelector('textarea[name="notes-' + key + '"]');
  return {{ key, label, verdict: v ? v.value : null, notes: n ? n.value.trim() : '' }};
}}
function generateResponse() {{
  const lines = ['# Library v5 Review','_Compiled from in-browser critique form._\\n','## Per-page verdicts'];
  let any = false;
  KEYS.forEach(([key, label]) => {{
    const s = collectSection(key, label);
    if (s.verdict || s.notes) {{
      any = true;
      lines.push('### ' + label);
      if (s.verdict) lines.push('- Verdict: **' + s.verdict + '**');
      if (s.notes) lines.push('- Notes: ' + s.notes);
    }}
  }});
  if (!any) lines.push('_(No per-page input — see Overall.)_');
  const o = document.getElementById('overall-notes').value.trim();
  if (o) {{ lines.push('\\n## Overall'); lines.push(o); }}
  document.getElementById('response-output').value = lines.join('\\n');
  const m = document.getElementById('response-modal');
  m.style.display = 'flex'; m.classList.remove('hidden');
}}
function closeModal() {{
  const m = document.getElementById('response-modal');
  m.style.display = 'none'; m.classList.add('hidden');
}}
function copyResponse() {{
  const ta = document.getElementById('response-output');
  ta.select(); ta.setSelectionRange(0, 999999);
  try {{
    navigator.clipboard.writeText(ta.value).then(() => {{
      const btn = document.getElementById('copy-btn');
      btn.innerHTML = '<span>✅</span><span>Copied!</span>';
      setTimeout(() => btn.innerHTML = '<span>📋</span><span>Copy</span>', 2000);
    }});
  }} catch(e) {{ document.execCommand('copy'); }}
}}
</script>
</body></html>""")


def main():
    OUT.write_text(build(), encoding="utf-8")
    print(f"✅ {OUT}")
    if sys.platform == "win32":
        os.startfile(str(OUT))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(OUT)])
    else:
        subprocess.run(["xdg-open", str(OUT)])
    print("🚀 Opened.")


if __name__ == "__main__":
    main()
