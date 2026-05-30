"""Library v6 report — word-cloud filtering + GitHub batch fix + post-ingest report."""

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
OUT = ROOT / "output" / "system_tour" / "library_v6.html"


PAGES = [
    {"title": "🔍 Word cloud — 'What's hot' on the Use Cases index",
       "shot": "v9_uc_cloud.png",
       "url": "/library/use-cases?ws=global",
       "what": "Size = frequency · Color = mode (default: frequency) · Tilt = age (newer upright). Mode + Dimension dropdowns in the header. Click any word to filter."},
    {"title": "🎯 Filtered + Refinement chips — narrowed by industry: B2B SaaS",
       "shot": "v9_uc_filtered.png",
       "url": "/library/use-cases?ws=global&industry=B2B+SaaS",
       "what": "Active filter strip + Refine-further chips show co-occurring tools / personas / complexities. Click chips to narrow more. Active term highlighted green in the cloud."},
    {"title": "🏠 Library home — word cloud preview",
       "shot": "v9_home_cloud.png",
       "url": "/library/?ws=global",
       "what": "Compact 'What's hot — industries' cloud sits above the use case lead. Click any word to drill in."},
    {"title": "🐙 GitHub batch ingest — ComposioHQ/awesome-claude-skills processed",
       "shot": "v9_ingest_progress.png",
       "url": "/library/ingest/<batch>?ws=global",
       "what": "31/31 files processed, all submitted. 'View full report' button appears when batch is done."},
    {"title": "📊 Post-ingest report — every asset that landed",
       "shot": "v9_ingest_report.png",
       "url": "/library/ingest/<batch>/report?ws=global",
       "what": "Hero stats: 31 submitted · 0 auto-vetted · 31 pending · 0 failed. 'Everything that was added' list with category/confidence/quality/tags. Sidebar: by category, quality breakdown, top tags, next-step actions."},
]


FIXES = [
    ("🐙 Default-branch fallback (master/main)",        "execution/products/library/fetcher.py",
     "fetch_github_tree() now tries `ref → main → master → repo's real default_branch` and returns the first non-empty tree. Composio's repo uses master — that's why the user saw zero matches."),
    ("📂 IngestItem carries the resolved ref",            "execution/products/library/ingest.py",
     "Before: per-file fetch defaulted to 'main' → 404 on master-branch repos. Now: every IngestItem stores the resolved ref, and `fetch_github_file(..., ref=item.ref)` uses it."),
    ("🪲 Jinja dict-attr collision → renamed `items` → `recent`", "execution/products/library/ingest.py + ingest_progress.html",
     "`status.items` resolved to Python's dict.items() method, not the items list. Causing the 500 error on every batch view. Renamed to `recent` + `all_results`."),
    ("📋 Widened file-pattern matcher",                    "execution/products/library/fetcher.py",
     "Added: /readme.md in subdirectories (awesome-list style), single-md skill files in category-named dirs. Composio's repo now yields 31 files."),
    ("🤔 Zero-items batch — clear diagnostic",             "app/templates/library/ingest_progress.html",
     "When walker returns 0 interesting files, the page renders an explanatory state with common causes + suggested workarounds."),
]


REPORT_SECTIONS = [
    ("Hero stats",       "Submitted / Auto-vetted / Pending / Failed (4 cards with the per-batch counts)"),
    ("Everything added", "Full list of submitted items: asset name (clickable), category badge, confidence %, quality %, tags, description preview, source link, warnings"),
    ("By category",      "Per-category count with click-through to /library/<category>"),
    ("Quality breakdown","High (≥70%) / Medium (40-69%) / Low (1-39%) / None — visualized as proportional bars"),
    ("Top tags",         "Most common tags across submissions"),
    ("Failed",           "Failed items with the exact error per row (if any failed)"),
    ("Next steps",       "Context-aware: links to pending queue, ingest more, or troubleshooting for zero-match batches"),
]


WORD_CLOUD_MODES = [
    ("Dimension: Industry",     "Default. Word cloud over use-case industries — see which verticals are over/under-represented."),
    ("Dimension: Persona",      "Cluster by extracted role keyword ('Proposal Manager', 'Sales Engineer', 'Founder')."),
    ("Dimension: Tool used",    "Most-referenced tools across all use cases."),
    ("Dimension: Tag",          "Raw tag distribution."),
    ("Dimension: Complexity",   "quick_win / moderate / advanced — 3-word cloud at most, but useful for big-picture skimming."),
    ("Color: Frequency",        "Neutral accent — size carries the count."),
    ("Color: Avg rating",       "Red→amber→green based on the mean star rating of UCs containing that word."),
    ("Color: Freshness",        "Faded gray → bright emerald based on average age (newer = brighter)."),
    ("Color: Vetted ratio",     "Gray → green based on % of UCs that are Colaberry-vetted."),
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
      <textarea name="notes-{key}" rows="2" placeholder="Notes..."
                class="w-full border border-slate-300 rounded p-2 text-xs focus:outline-none focus:ring-2 focus:ring-emerald-400"></textarea>
    </div>""")


def build():
    pages_html = "".join(
        dedent(f"""
        <section class="bg-white rounded-2xl shadow-md border border-slate-200 mb-6 overflow-hidden">
          <div class="p-5 border-b border-slate-200">
            <h3 class="text-xl font-bold">{html.escape(p["title"])}</h3>
            <div class="text-xs text-slate-500 mt-1 font-mono">localhost:8765{html.escape(p["url"])}</div>
            <p class="text-sm text-slate-600 mt-2"><strong>👀 What to look for:</strong> {html.escape(p["what"])}</p>
          </div>
          <img src="screenshots/{p["shot"]}" class="w-full block">
          {critique_form('page-' + p["shot"].replace(".png",""), p["title"])}
        </section>""")
        for p in PAGES
    )

    fixes_html = "".join(
        dedent(f"""
        <tr class="border-b border-slate-100">
          <td class="py-3 pr-4 align-top w-72">
            <div class="font-semibold text-sm">{html.escape(name)}</div>
            <div class="text-xs text-slate-500 font-mono mt-1">{html.escape(path)}</div>
            <div class="text-xs text-slate-400 mt-1">{loc_size(path)}</div>
          </td>
          <td class="py-3 align-top text-sm text-slate-700">{html.escape(desc)}</td>
        </tr>""")
        for name, path, desc in FIXES
    )

    report_html = "".join(
        f'<li class="mb-2"><strong>{html.escape(label)}</strong>: {html.escape(desc)}</li>'
        for label, desc in REPORT_SECTIONS
    )

    modes_html = "".join(
        f'<li class="mb-1"><strong>{html.escape(name)}</strong>: {html.escape(desc)}</li>'
        for name, desc in WORD_CLOUD_MODES
    )

    return dedent(f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>📚 Library v6 — Word Cloud + GitHub Batch Fix + Report</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}</style>
</head><body class="bg-slate-50">

<div class="bg-gradient-to-br from-emerald-700 via-emerald-800 to-slate-900 text-white py-14">
  <div class="max-w-5xl mx-auto px-6 text-center">
    <div class="text-6xl mb-3">🔍 ➜ 📊</div>
    <h1 class="text-5xl font-extrabold leading-tight">Word-cloud filtering<br>+ GitHub batch fixed<br>+ Post-ingest report</h1>
    <p class="text-xl text-slate-300 mt-4">
      Click any word in the cloud to filter. Drill in with refinement chips.<br>
      GitHub repos with master branch now work. Every batch ends with a clickable list of what landed.
    </p>
  </div>
</div>

<div class="max-w-6xl mx-auto px-6 py-10">

  <h2 class="text-3xl font-bold mb-3">🎯 What changed</h2>
  <div class="grid md:grid-cols-3 gap-5 mb-8">
    <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
      <div class="text-4xl mb-2">🔍</div>
      <div class="font-bold text-lg">Word cloud + chips</div>
      <p class="text-sm text-slate-600 mt-2">Visual filter on Use Cases index + Library home + category pages. 5 dimensions · 4 color modes. Click to narrow. Co-occurring terms surface as refine-further chips.</p>
    </div>
    <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
      <div class="text-4xl mb-2">🐙</div>
      <div class="font-bold text-lg">GitHub batch fixed</div>
      <p class="text-sm text-slate-600 mt-2">5 root-cause fixes (default-branch fallback, ref-aware fetcher, widened patterns, Jinja-collision rename, 0-item diagnostic). Composio's repo: 0/0 → 31/31 ✓.</p>
    </div>
    <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
      <div class="text-4xl mb-2">📊</div>
      <div class="font-bold text-lg">Post-ingest report</div>
      <p class="text-sm text-slate-600 mt-2">Every batch now ends with a rich summary: what was added (clickable), by category, quality breakdown, top tags, failures (if any), and context-aware next steps.</p>
    </div>
  </div>

  <!-- Walkthrough -->
  <h2 class="text-3xl font-bold mt-12 mb-3">🖼️ Page walkthrough</h2>
  {pages_html}

  <!-- Fixes table -->
  <h2 class="text-3xl font-bold mt-12 mb-3">🔧 Fixes — what was broken and what it does now</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden mb-6">
    <table class="w-full">
      <thead>
        <tr class="border-b-2 border-slate-200">
          <th class="text-left py-3 pr-4 text-xs uppercase text-slate-500 pl-5">Fix</th>
          <th class="text-left py-3 text-xs uppercase text-slate-500">Detail</th>
        </tr>
      </thead>
      <tbody>{fixes_html}</tbody>
    </table>
  </div>

  <!-- Report content -->
  <h2 class="text-3xl font-bold mt-12 mb-3">📋 What's in the post-ingest report</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
    <ul class="text-slate-700 list-disc ml-5">{report_html}</ul>
  </div>

  <!-- Cloud modes -->
  <h2 class="text-3xl font-bold mt-12 mb-3">🎨 Word cloud — dimensions + color modes</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
    <ul class="text-slate-700 list-disc ml-5">{modes_html}</ul>
  </div>

  <!-- Tests -->
  <h2 class="text-3xl font-bold mt-12 mb-3">🧪 Tests</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
    <ul class="text-slate-700 space-y-2 list-disc ml-5">
      <li>✅ Library tests: <strong>51/51 pass</strong> (33 classifier + 18 pipeline, with the renamed status key updated)</li>
      <li>✅ End-to-end: ingested <code>github.com/ComposioHQ/awesome-claude-skills</code> → 31/31 submitted, full report rendered</li>
    </ul>
  </div>

  <!-- Try it -->
  <h2 class="text-3xl font-bold mt-12 mb-3">🚀 Try it now</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
    <ul class="text-slate-700 space-y-2 list-disc ml-5">
      <li>🎯 <a href="http://localhost:8765/library/use-cases?ws=global" target="_blank" class="text-emerald-700 font-mono hover:underline">/library/use-cases</a> — full word cloud + filtering</li>
      <li>🔍 Click any word in the cloud to filter; the URL keeps the filter; click again (or the ✕) to clear</li>
      <li>📊 <a href="http://localhost:8765/library/ingest/1e599a3a-9bf/report?ws=global" target="_blank" class="text-emerald-700 font-mono hover:underline">view the awesome-claude-skills ingest report</a></li>
      <li>🐙 Re-ingest a fresh repo: paste any GitHub URL into <code>/library/ingest</code></li>
    </ul>
  </div>

  <h2 id="critique" class="text-3xl font-bold mt-12 mb-3">🎯 Overall verdict</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
    <textarea id="overall-notes" rows="6"
              placeholder="Anything still off? What's next?"
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
  const lines = ['# Library v6 Review','_Compiled from in-browser critique form._\\n','## Per-page verdicts'];
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
