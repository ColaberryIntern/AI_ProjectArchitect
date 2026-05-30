"""Build the Library Pivot report — captures the post-feedback rebuild:
Ops cut, Library upgraded to multi-tenant, vetted, ratings, comments,
submissions, weekly scanner, featured-of-the-day, asset detail pages.
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
OUT = ROOT / "output" / "system_tour" / "library_pivot.html"


PAGES = [
    {"title": "🏠 Library — overview (featured-of-the-day + scanner stats + pending count)",
       "shot": "lib_home_v3.png",
       "url": "/library/?ws=global",
       "what_to_look_for": "Featured 'MCP Filesystem Server' with vetted badge + 4.5 stars. 518 assets · 2 pending · 2 workspaces. Workspace picker in top-right nav."},
    {"title": "🛠️ Skills category — Colaberry vetted filter",
       "shot": "lib_skills_v3.png",
       "url": "/library/skills?ws=global",
       "what_to_look_for": "Filter chips at top: 'All' vs '✓ Colaberry vetted only'. Each row shows rating stars + 'Colaberry vetted' badge where applicable."},
    {"title": "📄 Asset detail page — MCP Filesystem Server",
       "shot": "lib_asset_v3.png",
       "url": "/library/skills/MCP%20Filesystem%20Server?ws=global&as=ali@colaberry.com",
       "what_to_look_for": "Breadcrumb · vetted badge · 'What it does' / 'How to use' / 'Example' sections · Discussion with real comments · Rate form (1-5 stars + note) · Curator decision form."},
    {"title": "➕ Submit to Library",
       "shot": "lib_submit_v3.png",
       "url": "/library/submit?ws=global",
       "what_to_look_for": "Form with: category picker, name, what-it-does, how-to-use, example, tags, source. Lands in pending review queue."},
    {"title": "⏳ Pending review queue",
       "shot": "lib_pending_v3.png",
       "url": "/library/pending?ws=global&as=ali@colaberry.com",
       "what_to_look_for": "Two real submissions: RFP Prompt (Maria) + Onboarding Workflow (Jose). Accept/reject + curator notes per item."},
    {"title": "🔍 Scanner candidates",
       "shot": "lib_candidates_v3.png",
       "url": "/library/candidates?ws=global",
       "what_to_look_for": "Weekly scan stats + 'Run scan now' button. Scans skill_catalog + plugins/ + config/library_sources.json. Idempotent."},
]


FILES = [
    ("🗄️ Library data store",       "execution/products/library/store.py",
     "Workspace-scoped metadata + ratings + comments + submissions + vetting. JSON-backed; easy to seed/inspect."),
    ("⭐ Featured-of-the-day",       "execution/products/library/featured.py",
     "Daily-seeded deterministic pick from rated/commented prompts + workflows + agents + skills."),
    ("🔍 Weekly scanner",            "execution/products/library/scanner.py",
     "Idempotent discovery. Sources: skill_catalog + plugins/ + config/library_sources.json. Stable SHA-256 candidate IDs."),
    ("📦 Library inventory",         "execution/products/library/inventory.py",
     "Reads all 15 categories from Platform Core registries. Updated to use load_registry()."),
    ("🌐 Library router (v2)",       "app/routers/library.py",
     "Full CRUD-lite — categories, asset detail, submit, pending review, candidates, scan, rate, comment, vet."),
    ("📚 Library shell base",        "app/templates/library/_library_base.html",
     "Multi-tenant workspace picker · vetted badge styles · filter chips · stars · featured 'of the day' hero."),
    ("🏠 Library home",              "app/templates/library/home.html",
     "Featured-of-the-day hero + 4 stat tiles + 15 category tiles + contribute/scanner mini-cards."),
    ("📋 Category page",             "app/templates/library/category.html",
     "Filter by 'Colaberry vetted'. Each row is a clickable link to detail."),
    ("📄 Asset detail page",         "app/templates/library/asset.html",
     "Breadcrumb · vetted badge · what/how/example · ratings · comments · curator decision."),
    ("➕ Submit form",                "app/templates/library/submit.html",
     "Category · name · what-it-does · how-to-use · example · tags · source."),
    ("🎉 Submit thanks",             "app/templates/library/submit_thanks.html",
     "Confirmation + submission summary + links to next actions."),
    ("⏳ Pending review",            "app/templates/library/pending.html",
     "All pending submissions with accept/reject form per item."),
    ("🔍 Candidates",                "app/templates/library/candidates.html",
     "Scanner output: name · description · discovered_by · source · status."),
    ("🔀 Switcher updated",          "app/templates/_platform/_switcher.html",
     "Ops removed. Now: 📐 Architect / 📚 Library only."),
    ("⚠️ Ops deprecation banner",    "app/templates/ops/_ops_base.html",
     "Banner: 'Ops product is deprecated — try Library'. Routes still resolve."),
]


def fmt_kv(p: str) -> str:
    full = ROOT / p
    if full.exists() and full.is_file():
        loc = len(full.read_text(encoding="utf-8", errors="replace").splitlines())
        return f"{loc} LOC · {full.stat().st_size/1024:.1f} KB"
    return "—"


def critique_form(key: str, label: str) -> str:
    safe = html.escape(label)
    return dedent(f"""
    <div class="bg-emerald-50 p-4 border-t border-slate-200" style="border-left:4px solid #1a7f37;">
      <div class="text-sm font-semibold mb-2 text-slate-800">💬 {safe}</div>
      <div class="flex flex-wrap gap-3 mb-2 text-sm">
        <label class="flex items-center gap-1"><input type="radio" name="verdict-{key}" value="approved" class="text-emerald-600"> ✅ Approved</label>
        <label class="flex items-center gap-1"><input type="radio" name="verdict-{key}" value="approved-with-notes" class="text-amber-600"> 🟡 With notes</label>
        <label class="flex items-center gap-1"><input type="radio" name="verdict-{key}" value="needs-work" class="text-rose-600"> 🔴 Needs work</label>
        <label class="flex items-center gap-1"><input type="radio" name="verdict-{key}" value="cut-it" class="text-slate-600"> 🗑️ Cut it</label>
      </div>
      <textarea name="notes-{key}" rows="2"
                placeholder="Notes..."
                class="w-full border border-slate-300 rounded p-2 text-xs focus:outline-none focus:ring-2 focus:ring-emerald-400"></textarea>
    </div>""")


def build():
    pages_html = ""
    for p in PAGES:
        pages_html += dedent(f"""
        <section class="bg-white rounded-2xl shadow-md border border-slate-200 mb-6 overflow-hidden">
          <div class="p-5 border-b border-slate-200">
            <h3 class="text-xl font-bold">{html.escape(p["title"])}</h3>
            <div class="text-xs text-slate-500 mt-1 font-mono">
              <a href="http://localhost:8765{html.escape(p["url"])}" target="_blank" class="text-emerald-700 hover:underline">localhost:8765{html.escape(p["url"])} ↗</a>
            </div>
            <p class="text-sm text-slate-600 mt-2"><strong>👀 What to look for:</strong> {html.escape(p["what_to_look_for"])}</p>
          </div>
          <img src="screenshots/{p["shot"]}" class="w-full block">
          {critique_form('page-' + p["shot"].replace(".png",""), p["title"])}
        </section>""")

    files_html = ""
    for emoji_title, path, desc in FILES:
        size = fmt_kv(path)
        files_html += dedent(f"""
        <tr class="border-b border-slate-100">
          <td class="py-3 pr-4 align-top">
            <div class="font-semibold text-sm">{html.escape(emoji_title)}</div>
            <div class="text-xs text-slate-500 font-mono mt-1">{html.escape(path)}</div>
          </td>
          <td class="py-3 pr-4 align-top text-xs text-slate-600 w-32">{size}</td>
          <td class="py-3 align-top text-sm text-slate-700">{html.escape(desc)}</td>
        </tr>""")

    return dedent(f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>📚 Library Pivot — Ops cut, Library expanded</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}</style>
</head><body class="bg-slate-50">

<div class="bg-gradient-to-br from-emerald-700 via-emerald-800 to-slate-900 text-white py-14">
  <div class="max-w-5xl mx-auto px-6 text-center">
    <div class="text-6xl mb-3">🗑️ ➜ 📚</div>
    <h1 class="text-5xl font-extrabold leading-tight">Ops cut. Library doubled down.</h1>
    <p class="text-xl text-slate-300 mt-4">
      Multi-tenant. Vetted by Colaberry. Submittable. Rated. Discussed. Auto-discovered weekly.<br>
      Every asset has a "what / how / example".
    </p>
    <div class="grid grid-cols-4 gap-3 mt-8">
      <div class="bg-white/10 rounded-xl p-4"><div class="text-3xl">🏢</div><div class="font-bold mt-1">Multi-tenant</div><div class="text-xs opacity-80">workspace picker · per-workspace data</div></div>
      <div class="bg-white/10 rounded-xl p-4"><div class="text-3xl">✓</div><div class="font-bold mt-1">Vetted</div><div class="text-xs opacity-80">curator decision + filter</div></div>
      <div class="bg-white/10 rounded-xl p-4"><div class="text-3xl">⭐</div><div class="font-bold mt-1">Of the day</div><div class="text-xs opacity-80">deterministic daily pick</div></div>
      <div class="bg-white/10 rounded-xl p-4"><div class="text-3xl">🔍</div><div class="font-bold mt-1">Weekly scanner</div><div class="text-xs opacity-80">idempotent discovery</div></div>
    </div>
  </div>
</div>

<div class="max-w-7xl mx-auto px-6 py-10">

  <!-- What changed since last review -->
  <h2 class="text-3xl font-bold mb-3">🎯 What changed since your last review</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
    <div class="grid md:grid-cols-2 gap-5">
      <div>
        <div class="text-xs font-bold uppercase text-rose-600 mb-2">🗑️ Cut</div>
        <ul class="text-slate-700 space-y-1 list-disc ml-5 text-sm">
          <li><strong>Ops product</strong> removed from the switcher</li>
          <li>Ops pages still resolve for legacy bookmarks</li>
          <li>Banner on every Ops page redirects to Library</li>
        </ul>
      </div>
      <div>
        <div class="text-xs font-bold uppercase text-emerald-600 mb-2">📚 Built</div>
        <ul class="text-slate-700 space-y-1 list-disc ml-5 text-sm">
          <li><strong>Multi-tenancy</strong> — workspace picker in nav, per-workspace data</li>
          <li><strong>"Colaberry vetted" system</strong> — badge + filter chip + curator decision form</li>
          <li><strong>Per-asset detail pages</strong> — what/how/example/ratings/comments</li>
          <li><strong>Rating + comment forms</strong> with author attribution</li>
          <li><strong>"Add to Library" submission flow</strong> → pending review queue</li>
          <li><strong>Weekly auto-scanner</strong> for new external assets (idempotent)</li>
          <li><strong>"Prompt/Workflow of the day"</strong> deterministic daily pick on home</li>
        </ul>
      </div>
    </div>
  </div>

  <!-- Pages walkthrough -->
  <h2 class="text-3xl font-bold mt-10 mb-3">🖼️ Page-by-page walkthrough</h2>
  <p class="text-slate-600 mb-6">Click any URL to poke around the live page. Critique boxes per page.</p>
  {pages_html}

  <!-- Files built -->
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
      <li>✅ <strong>3 layer manifest tests pass</strong> (no new module without a layer; no phantom assignments).</li>
      <li>✅ <strong>7 integration tests pass</strong> (full pipeline regression check).</li>
      <li>🔁 Full test suite (2138+ tests) running in background — last result will be reported separately.</li>
      <li>♿ <strong>Accessibility CI</strong> ready (<code>tests/ui/test_accessibility.py</code>) — opt-in via <code>COLABERRY_A11Y_URL</code>.</li>
    </ul>
  </div>

  <!-- Demo data -->
  <h2 class="text-3xl font-bold mt-12 mb-3">🌱 Seeded demo data (so the pages aren't empty)</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
    <ul class="text-slate-700 space-y-2 list-disc ml-5">
      <li>📄 <strong>MCP Filesystem Server</strong> — first skill, marked Colaberry-vetted by ali@colaberry.com, 2 ratings (5★ + 4★ avg 4.5), 2 comments.</li>
      <li>⏳ <strong>RFP Summary Prompt v2</strong> — submitted by maria@colaberry.com, pending review.</li>
      <li>⏳ <strong>Client onboarding kickoff workflow</strong> — submitted by jose@colaberry.com, pending review.</li>
      <li>🔍 Scanner ran once: 0 new candidates this run (everything in skill_catalog already mirrors to Library).</li>
    </ul>
    <p class="text-sm text-slate-600 mt-3">All seed data lives at <code>output/library/global/</code> — easy to inspect, delete, or extend.</p>
  </div>

  <!-- Overall -->
  <h2 id="critique" class="text-3xl font-bold mt-12 mb-3">🎯 Overall verdict</h2>
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
    <textarea id="overall-notes" rows="6"
              placeholder="Anything still off? What's next? What's missing that you want for the team?"
              class="w-full border border-slate-300 rounded-md p-3 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"></textarea>
    <div class="mt-5 flex items-center gap-3">
      <button onclick="generateResponse()" class="bg-emerald-600 hover:bg-emerald-700 text-white font-semibold px-6 py-3 rounded-lg shadow-md flex items-center gap-2 text-lg">
        <span>🎯</span> Generate Response for Claude
      </button>
    </div>
  </div>

</div>

<!-- Modal -->
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
const PAGE_KEYS = {json.dumps([("page-" + p["shot"].replace(".png",""), p["title"]) for p in PAGES])};
function collectSection(key, label) {{
  const v = document.querySelector('input[name="verdict-' + key + '"]:checked');
  const n = document.querySelector('textarea[name="notes-' + key + '"]');
  return {{ key, label, verdict: v ? v.value : null, notes: n ? n.value.trim() : '' }};
}}
function generateResponse() {{
  const lines = ['# Library Pivot Review','_Compiled from in-browser critique form._\\n','## Per-page verdicts'];
  let any = false;
  PAGE_KEYS.forEach(([key, label]) => {{
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
