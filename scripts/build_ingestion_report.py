"""Library v3 report — classification fix + ingestion pipeline.

Captures the before/after for the broken Skills tab + walks the new
fetch→parse→classify→enrich→submit pipeline.
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
OUT = ROOT / "output" / "system_tour" / "library_v3.html"


CATEGORY_BEFORE_AFTER = [
    ("Skills",     500, 115, "Was: 500 items (all MCP servers + tools + frameworks). Now: 115 actual skills (Claude tools, LangChain tools, etc.)"),
    ("MCP Servers",   0, 363, "Was: 0 (not classified). Now: 363 real MCP servers correctly routed."),
    ("Agents",        0,  17, "Was: 0. Now: 17 — AutoGPT, CrewAI, LangChain, LangGraph, Semantic Kernel, Haystack, DSPy, Phidata, etc."),
    ("Prompts",       0,   1, "Was: 0. Now: 1 — Claude Prompt Caching."),
]


PAGES = [
    {"title": "🏠 Library home — corrected counts + featured-of-the-day",
       "shot": "v4_lib_home.png",
       "url": "/library/?ws=global",
       "what": "514 total assets across 15 categories. Skills/Agents/Prompts/MCP each show their TRUE count. Featured today: Claude Prompt Caching."},
    {"title": "🛠️ Skills — now shows actual skills (no more MCP contamination)",
       "shot": "v4_lib_skills.png",
       "url": "/library/skills?ws=global",
       "what": "115 rows: Claude Tool Use, Claude Computer Use, Claude Vision, Claude Message Batches, LangChain tools, etc. All legit skills."},
    {"title": "🔌 MCP Servers — newly populated (363)",
       "shot": "v4_lib_mcp.png",
       "url": "/library/mcp?ws=global",
       "what": "Every MCP server from the catalog routed here. Previously hidden under 'Skills'."},
    {"title": "🤖 Agents — newly populated (17)",
       "shot": "v4_lib_agents.png",
       "url": "/library/agents?ws=global",
       "what": "AI agent frameworks (AutoGPT, CrewAI, LangChain, Semantic Kernel…) plus any native agent_registry agents."},
    {"title": "🌐 Ingest from URL or GitHub",
       "shot": "v4_lib_ingest.png",
       "url": "/library/ingest?ws=global&as=ali@colaberry.com",
       "what": "Two forms: Single URL + GitHub repo (batch). Pipeline stages explained at the bottom. Recent batches list."},
]


PIPELINE_STAGES = [
    ("1. Fetch",     "🌐", "execution/products/library/fetcher.py",
     "HTTP GET via stdlib urllib. Auto-rewrites github.com/.../blob/ → raw.githubusercontent. GitHub tree API + raw file API. Honors GITHUB_TOKEN."),
    ("2. Parse",     "📑", "execution/products/library/parser.py",
     "HTML (title/meta/h1/og) · Markdown (frontmatter/h1/code blocks) · JSON manifests (mcp.json/package.json/etc.). No external deps."),
    ("3. Classify",  "🧭", "execution/products/library/classifier.py",
     "Rule-based: kind > category > URL hint > name pattern > tags > manifest shape > fallback. Returns category + confidence + reason chain."),
    ("4. Enrich",    "✨", "execution/products/library/enricher.py",
     "Pulls name/description/how_to_use/example/tags/version/owner. Quality score 0-1. Optional LLM hook via LIBRARY_ENRICH_WITH_LLM=1."),
    ("5. Submit",    "📥", "execution/products/library/store.py",
     "Lands in pending review queue. Auto-vets if source matches config/library_trusted_sources.json AND confidence ≥ 0.75."),
]


FILES = [
    ("🧭 Classifier",                 "execution/products/library/classifier.py",        "Routes any raw asset dict to one of 15 Library categories. 33 table-test cases."),
    ("📦 Inventory (re-routed)",      "execution/products/library/inventory.py",         "Every list_*() function now uses classified buckets. Counts auto-correct."),
    ("🌐 Fetcher",                    "execution/products/library/fetcher.py",           "URL + GitHub tree walker + file-pattern matcher. Stdlib only."),
    ("📑 Parser",                     "execution/products/library/parser.py",            "HTML / Markdown / JSON manifest. ParsedSurface output."),
    ("✨ Enricher",                    "execution/products/library/enricher.py",          "Calls classifier; extracts how_to_use / example; quality score; LLM hook."),
    ("🛡️ Trusted-sources",            "execution/products/library/trusted.py",           "Regex allowlist + confidence-threshold gate for auto-vetting."),
    ("⚙️ Ingest orchestrator",        "execution/products/library/ingest.py",            "Threadpool batch runner. Per-item progress in items.jsonl. Single-URL + GitHub modes."),
    ("🌐 /library/ingest router",     "app/routers/library.py",                          "5 new endpoints: form, /url POST, /github POST, /<batch_id>, /<batch_id>/json."),
    ("📋 Ingest form template",       "app/templates/library/ingest.html",               "Two-column form (URL + GitHub) + recent batches + pipeline explainer."),
    ("📊 Progress template",          "app/templates/library/ingest_progress.html",      "Live progress bar + per-item results. Polls JSON endpoint every 1.5s."),
    ("📁 Trusted-sources config",     "config/library_trusted_sources.json",             "Allowlist: colaberry/*, modelcontextprotocol/servers, anthropic-cookbook."),
    ("🧪 Classifier tests",           "tests/execution/products/test_library_classifier.py", "33 tests: 28 table cases + 5 confidence/reasoning/bucket tests."),
    ("🧪 Pipeline tests",             "tests/execution/products/test_library_pipeline.py",   "18 tests: parser, enricher, fetcher URL parsing, trusted-sources, e2e ingest."),
]


def loc_size(path: str) -> str:
    p = ROOT / path
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
    before_after_rows = "".join(
        f'<tr class="border-b border-slate-100"><td class="py-3 pr-4 font-semibold">{html.escape(name)}</td>'
        f'<td class="py-3 pr-4 text-rose-700">{before}</td>'
        f'<td class="py-3 pr-4 text-emerald-700 font-bold">{after}</td>'
        f'<td class="py-3 text-sm text-slate-600">{html.escape(note)}</td></tr>'
        for name, before, after, note in CATEGORY_BEFORE_AFTER
    )

    pages_html = "".join(
        dedent(f"""
        <section class="bg-white rounded-2xl shadow-md border border-slate-200 mb-6 overflow-hidden">
          <div class="p-5 border-b border-slate-200">
            <h3 class="text-xl font-bold">{html.escape(p["title"])}</h3>
            <div class="text-xs text-slate-500 mt-1 font-mono">
              <a href="http://localhost:8765{html.escape(p["url"])}" target="_blank" class="text-emerald-700 hover:underline">localhost:8765{html.escape(p["url"])} ↗</a>
            </div>
            <p class="text-sm text-slate-600 mt-2"><strong>👀 What to look for:</strong> {html.escape(p["what"])}</p>
          </div>
          <img src="screenshots/{p["shot"]}" class="w-full block">
          {critique_form('page-' + p["shot"].replace(".png",""), p["title"])}
        </section>""")
        for p in PAGES
    )

    stages_html = "".join(
        dedent(f"""
        <div class="bg-white border border-slate-200 rounded-xl p-4 mb-3 flex items-start gap-4">
          <div class="text-4xl flex-none">{emoji}</div>
          <div class="flex-1">
            <div class="font-bold text-lg">{html.escape(name)}</div>
            <div class="text-xs text-slate-500 font-mono">{html.escape(path)}</div>
            <p class="text-sm text-slate-700 mt-1">{html.escape(desc)}</p>
          </div>
        </div>""")
        for name, emoji, path, desc in PIPELINE_STAGES
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
<title>📚 Library v3 — Classification fixed + Ingestion pipeline</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}</style>
</head><body class="bg-slate-50">

<div class="bg-gradient-to-br from-emerald-700 via-emerald-800 to-slate-900 text-white py-14">
  <div class="max-w-5xl mx-auto px-6 text-center">
    <div class="text-6xl mb-3">📚 ➜ 🌐</div>
    <h1 class="text-5xl font-extrabold leading-tight">Skills are skills now.<br>And anything can be ingested.</h1>
    <p class="text-xl text-slate-300 mt-4">
      Classification fixed. Skills 500→115. MCP 0→363. Agents 0→17.<br>
      Plus: paste a URL or GitHub repo → fetch → parse → classify → enrich → submit.
    </p>
  </div>
</div>

<div class="max-w-6xl mx-auto px-6 py-10">

  <!-- The fix -->
  <h2 class="text-3xl font-bold mb-3">🎯 The classification fix</h2>
  <div class="bg-white rounded-2xl shadow-md border border-slate-200 overflow-hidden mb-6">
    <table class="w-full">
      <thead>
        <tr class="border-b-2 border-slate-200 bg-slate-50">
          <th class="text-left py-3 pr-4 pl-5 text-xs uppercase text-slate-500">Category</th>
          <th class="text-left py-3 pr-4 text-xs uppercase text-rose-600">Before</th>
          <th class="text-left py-3 pr-4 text-xs uppercase text-emerald-600">After</th>
          <th class="text-left py-3 text-xs uppercase text-slate-500">Why</th>
        </tr>
      </thead>
      <tbody class="pl-5">{before_after_rows}</tbody>
    </table>
  </div>
  {critique_form('classification', 'Classification fix')}

  <!-- Pipeline diagram -->
  <h2 class="text-3xl font-bold mt-12 mb-3">⚙️ The 5-stage ingestion pipeline</h2>
  <p class="text-slate-600 mb-4">Same pipeline for single URL and GitHub repo batch. Per-item progress observable.</p>
  {stages_html}
  {critique_form('pipeline', '5-stage ingestion pipeline')}

  <!-- Page walkthrough -->
  <h2 class="text-3xl font-bold mt-12 mb-3">🖼️ Page-by-page walkthrough</h2>
  {pages_html}

  <!-- Files -->
  <h2 class="text-3xl font-bold mt-12 mb-3">📦 Files built this round</h2>
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

  <!-- Tests -->
  <h2 class="text-3xl font-bold mt-12 mb-3">🧪 Tests</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
    <ul class="text-slate-700 space-y-2 list-disc ml-5">
      <li>✅ <strong>33 classifier tests pass</strong> (28 table cases + 5 confidence/reasoning/bucket)</li>
      <li>✅ <strong>18 pipeline tests pass</strong> (parser, enricher, fetcher URL parsing, trusted-sources, e2e ingest)</li>
      <li>✅ <strong>Total +51 new tests</strong> covering this round of work</li>
      <li>🔁 Full suite running in background — final count will be reported separately</li>
    </ul>
  </div>

  <!-- Try it -->
  <h2 class="text-3xl font-bold mt-12 mb-3">🚀 Try it</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
    <p class="mb-3">Real things you can do right now (server is running on port 8765):</p>
    <ul class="text-slate-700 space-y-2 list-disc ml-5">
      <li>Open <a href="http://localhost:8765/library/?ws=global" target="_blank" class="text-emerald-700 font-mono hover:underline">localhost:8765/library/</a> — see the corrected counts</li>
      <li>Open <a href="http://localhost:8765/library/ingest?ws=global&as=ali@colaberry.com" target="_blank" class="text-emerald-700 font-mono hover:underline">/library/ingest</a> — paste any URL into "Single URL" or any GitHub repo into "GitHub repo (batch)"</li>
      <li>Try GitHub: <code>https://github.com/modelcontextprotocol/servers</code> — should auto-vet (it's in the trusted-sources allowlist)</li>
      <li>Watch the per-item progress page update live every 1.5 seconds</li>
    </ul>
  </div>

  <!-- Overall -->
  <h2 id="critique" class="text-3xl font-bold mt-12 mb-3">🎯 Overall verdict</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
    <textarea id="overall-notes" rows="6"
              placeholder="Anything still off? What should I tune next?"
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
const KEYS = {json.dumps([("page-" + p["shot"].replace(".png",""), p["title"]) for p in PAGES] + [
    ("classification", "Classification fix"),
    ("pipeline", "5-stage ingestion pipeline"),
])};
function collectSection(key, label) {{
  const v = document.querySelector('input[name="verdict-' + key + '"]:checked');
  const n = document.querySelector('textarea[name="notes-' + key + '"]');
  return {{ key, label, verdict: v ? v.value : null, notes: n ? n.value.trim() : '' }};
}}
function generateResponse() {{
  const lines = ['# Library v3 Review','_Compiled from in-browser critique form._\\n','## Per-section verdicts'];
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
  if (!any) lines.push('_(No per-section input — see Overall.)_');
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
