"""Library v4 report — enrichment + sidebar nav + global search.

Three big lifts captured:
    1. Asset enrichment (fetches README/manifest/code from source URLs)
    2. Sidebar navigation redesign (3 grouped category sections + top bar)
    3. Global search across all categories
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
OUT = ROOT / "output" / "system_tour" / "library_v4.html"


PAGES = [
    {"title": "🏠 Home — new sidebar layout, sticky top bar with global search + Add menu",
       "shot": "v5_home.png",
       "url": "/library/?ws=global",
       "what": "Sidebar groups: Reusable Assets (Skills/Agents/Prompts/MCP/Caps/Templates), Operations (Workflows/Projections/Recovery/Chaos), Governance (Policies/Scorecards/Evals/Connectors/Adapters), Admin. Count on every category. Top: brand · search · + Add · ⏳ pending · workspace · 📐 Architect."},
    {"title": "📄 MCP Filesystem Server — fully enriched detail page",
       "shot": "v5_asset_filesys.png",
       "url": "/library/mcp/MCP%20Filesystem%20Server?ws=global&as=ali@colaberry.com",
       "what": "14 KB fetched README rendered as markdown · MIT license auto-detected · 4 dependencies pulled from package.json (@modelcontextprotocol/sdk, diff, glob, minimatch) · Refresh button · enrichment timestamp."},
    {"title": "🤖 CrewAI Multi-Agent Framework — agents bucket, enriched",
       "shot": "v5_asset_crewai.png",
       "url": "/library/agents/CrewAI%20Multi-Agent%20Framework?ws=global&as=ali@colaberry.com",
       "what": "34 KB README + code sample + dependencies. Demonstrates the enricher working on a different repo layout."},
    {"title": "🔍 Search — 'slack' returns 6 ranked matches across categories",
       "shot": "v5_search_slack.png",
       "url": "/library/search?q=slack&ws=global",
       "what": "Each hit shows snippet, tags, category, relevance score. MCP Slack Server (11.0) · n8n Slack Node (10.0) · Zapier (10.0) · Slack-via-Zencoder (10.0) · Multi-Channel Hub (2.0) · MCP GitHub Server (matched in README)."},
    {"title": "🔍 Search — 'mcp' returns broader pattern matches",
       "shot": "v5_search_mcp.png",
       "url": "/library/search?q=mcp&ws=global",
       "what": "Searches across name + tags + description + enriched README content."},
    {"title": "🛠️ Skills — real skills only (115 entries, not 500)",
       "shot": "v5_skills.png",
       "url": "/library/skills?ws=global",
       "what": "Classifier left only true skills here. Claude tools, LangChain tools, etc. MCP servers + agent frameworks routed elsewhere."},
    {"title": "🔌 MCP Servers — 363 correctly-classified entries",
       "shot": "v5_mcp.png",
       "url": "/library/mcp?ws=global",
       "what": "All 363 MCP servers in their proper bucket. Each clickable to a detail page; each can be enriched on demand."},
    {"title": "🌐 Ingest — pipeline UI unchanged from v3, now feeds enriched assets",
       "shot": "v5_ingest.png",
       "url": "/library/ingest?ws=global&as=ali@colaberry.com",
       "what": "Single URL or GitHub repo. Same fetch→parse→classify→enrich→submit pipeline; landing assets get rich content from the start."},
]


PIPELINE_STAGES = [
    ("1. Strategy", "🧭", "_resolve_strategy(source_url)",
     "Decides fetch mode: github_repo / github_subdir / github_file / raw_markdown / webpage."),
    ("2. README",   "📄", "loop _README_CANDIDATES (subdir-prefix first)",
     "Tries README.md / README / README.MD / README.rst variants under the subdir, then at repo root."),
    ("3. Manifest", "📦", "_MANIFEST_CANDIDATES",
     "mcp.json, package.json, pyproject.toml, manifest.json, skill.json, agent.json. Parsed; dependencies extracted."),
    ("4. License",  "📜", "_LICENSE_CANDIDATES + _extract_license()",
     "Looks for LICENSE/LICENSE.md and scans for SPDX identifiers (MIT, Apache-2, BSD-3, etc.)."),
    ("5. Code sample", "💻", "_pick_sample_code_file(tree)",
     "Walks the GitHub tree; picks one source file (≤50 KB), preferring src/ or main files, avoiding tests."),
    ("6. Repo stats",  "📊", "_fetch_repo_stats() (needs GITHUB_TOKEN)",
     "Stars, forks, last commit, language, default branch. Graceful no-op without token."),
    ("7. Extract", "✨", "_extract_install_steps + _extract_examples",
     "Parses 'Install/Setup/Getting Started' sections for steps; pulls fenced code blocks for examples."),
    ("8. Snapshot + persist", "💾", "_write_snapshot + store.save_metadata",
     "Raw README saved under output/library/_snapshots/. Structured fields persisted in asset metadata JSON."),
]


FILES = [
    ("📦 Enrichment data model",         "execution/products/library/store.py",
     "AssetMetadata extended with: enrichment_state, enriched_at, readme_markdown, install_steps, examples, code_samples, license, languages, dependencies, repo_stats, snapshot_path. Backward-compatible loader drops unknown keys."),
    ("🔧 Enrichment job",                "execution/products/library/enrichment_job.py",
     "URL → strategy → fetch (README/manifest/license/code/stats) → extract → snapshot → persist. Handles github_repo, github_subdir, github_file, raw_markdown, webpage."),
    ("⚙️ Bulk enrichment driver",         "execution/products/library/ingest.py",
     "Added enrich_category() — threadpool runs over every asset in a category, same progress UI as ingest."),
    ("🔍 Global search",                 "execution/products/library/search.py",
     "Scores name/tags > description > README content. No external index — < 50 ms on 500 items."),
    ("🌐 /library/search route",          "app/routers/library.py",
     "Plus /library/{cat}/{asset}/enrich POST, /library/enrich/{cat} POST for bulk."),
    ("🧭 Sidebar nav redesign",          "app/templates/library/_library_base.html",
     "Grid layout: top bar + left sidebar (240px). 3 grouped category sections + Admin. Search input + Add dropdown + workspace picker + pending badge + product switcher. Mobile-responsive."),
    ("📄 Rich asset detail page",        "app/templates/library/asset.html",
     "Rendered README (marked.js) · code blocks with Prism highlighting · install steps as checklist · dependencies · repo stats · refresh button · enrichment pill · provenance card."),
    ("🔍 Search results template",       "app/templates/library/search.html",
     "Results list with snippet preview, category, relevance score, tags. Vetted-only filter."),
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
        <div class="bg-white border border-slate-200 rounded-xl p-4 mb-2 flex items-start gap-4">
          <div class="text-3xl flex-none">{emoji}</div>
          <div class="flex-1">
            <div class="font-bold text-sm">{html.escape(name)}</div>
            <div class="text-xs text-slate-500 font-mono">{html.escape(code)}</div>
            <p class="text-sm text-slate-700 mt-1">{html.escape(desc)}</p>
          </div>
        </div>""")
        for name, emoji, code, desc in PIPELINE_STAGES
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
<title>📚 Library v4 — Enriched + Redesigned</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}</style>
</head><body class="bg-slate-50">

<div class="bg-gradient-to-br from-emerald-700 via-emerald-800 to-slate-900 text-white py-14">
  <div class="max-w-5xl mx-auto px-6 text-center">
    <div class="text-6xl mb-3">📄 ➜ 📚</div>
    <h1 class="text-5xl font-extrabold leading-tight">Thin URL records ➜<br>Fully-enriched assets.</h1>
    <p class="text-xl text-slate-300 mt-4">
      Real READMEs · code samples · license · dependencies · repo stats.<br>
      Plus: sidebar nav with grouped categories. Plus: global search.
    </p>
  </div>
</div>

<div class="max-w-6xl mx-auto px-6 py-10">

  <!-- The big lifts -->
  <h2 class="text-3xl font-bold mb-3">🎯 What's new</h2>
  <div class="grid md:grid-cols-3 gap-5 mb-8">
    <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
      <div class="text-4xl mb-2">📚</div>
      <div class="font-bold text-lg">Enrichment</div>
      <p class="text-sm text-slate-600 mt-2">Every asset can now fetch its source URL, snapshot the README, parse install/example sections, detect license, extract dependencies, grab a code sample, pull repo stats.</p>
    </div>
    <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
      <div class="text-4xl mb-2">🧭</div>
      <div class="font-bold text-lg">Sidebar nav</div>
      <p class="text-sm text-slate-600 mt-2">11-item top nav → 240 px sidebar with 3 grouped sections + admin. Top bar carries brand · search · + Add menu · pending badge · workspace · product switcher.</p>
    </div>
    <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
      <div class="text-4xl mb-2">🔍</div>
      <div class="font-bold text-lg">Global search</div>
      <p class="text-sm text-slate-600 mt-2">Searches across all 15 categories at once. Name/tags > description > README content. Snippet preview, relevance score, click-through to detail.</p>
    </div>
  </div>

  <!-- Pipeline -->
  <h2 class="text-3xl font-bold mt-12 mb-3">⚙️ Enrichment pipeline (8 stages)</h2>
  <p class="text-slate-600 mb-4">Per asset. Idempotent. Refreshable on demand from the detail page.</p>
  {stages_html}

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

  <!-- Tests -->
  <h2 class="text-3xl font-bold mt-12 mb-3">🧪 Tests</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
    <ul class="text-slate-700 space-y-2 list-disc ml-5">
      <li>✅ <strong>51 library tests pass</strong> — classifier (33) + pipeline (18)</li>
      <li>✅ <strong>Full test suite: 2192 passed, 30 skipped, 0 failed</strong> (20 min). Zero regressions.</li>
      <li>♿ Accessibility CI still ready to gate (env <code>COLABERRY_A11Y_URL=http://localhost:8765</code>)</li>
    </ul>
  </div>

  <!-- Try it -->
  <h2 class="text-3xl font-bold mt-12 mb-3">🚀 Try it now</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
    <ul class="text-slate-700 space-y-2 list-disc ml-5">
      <li>📚 <a href="http://localhost:8765/library/?ws=global" target="_blank" class="text-emerald-700 font-mono hover:underline">localhost:8765/library/</a> — new sidebar layout</li>
      <li>🔍 Hit <code>Ctrl+K</code> anywhere → focuses the global search</li>
      <li>📄 Click any enriched MCP server (filesystem, github, slack) to see the rendered README + dependencies + license</li>
      <li>🔄 On any asset, click the <strong>Refresh / Enrich now</strong> button to (re)fetch its source URL</li>
      <li>🌐 Ingest a brand new GitHub repo: <code>github.com/anthropics/anthropic-cookbook</code></li>
    </ul>
  </div>

  <!-- Overall -->
  <h2 id="critique" class="text-3xl font-bold mt-12 mb-3">🎯 Overall verdict</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
    <textarea id="overall-notes" rows="6"
              placeholder="Anything to refine? What's next?"
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
  const lines = ['# Library v4 Review','_Compiled from in-browser critique form._\\n','## Per-page verdicts'];
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
