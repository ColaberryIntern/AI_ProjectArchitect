"""Build a gallery HTML showing the live UI screenshots captured from the
running app, with critique forms per page + a Generate Response button.

Reads:  output/system_tour/live_pages.json
Writes: output/system_tour/live_ui.html
Opens it in the default browser.
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
TOUR = ROOT / "output" / "system_tour"
INDEX = TOUR / "live_pages.json"
OUT = TOUR / "live_ui.html"


# Map page slugs → phase that introduced them (for grouping)
PHASE_MAP = {
    "p0_landing": ("0", "📐 Phase 0 — Project Architect"),
    "ops_home": ("1", "🏗️ Phase 1 — Foundation"),
    "ops_workspaces": ("1", "🏗️ Phase 1 — Foundation"),
    "ops_dashboard": ("1", "🏗️ Phase 1 — Foundation"),
    "ops_discovery": ("2", "🧠 Phase 2 — Semantic Intelligence"),
    "ops_pipelines": ("3", "🎼 Phase 3 — Orchestration"),
    "ops_builder": ("4", "🛠️ Phase 4 — Lifecycle + Builder"),
    "ops_recommend": ("4", "🛠️ Phase 4 — Lifecycle + Builder"),
    "ops_optimizer": ("4", "🛠️ Phase 4 — Lifecycle + Builder"),
    "ops_analytics": ("4", "🛠️ Phase 4 — Lifecycle + Builder"),
    "ops_executive": ("4", "🛠️ Phase 4 — Lifecycle + Builder"),
    "ops_copilot": ("8", "🤖 Phase 8 — Autonomous + Collab"),
    "ops_assistant": ("8", "🤖 Phase 8 — Autonomous + Collab"),
    "ops_audit": ("8", "🤖 Phase 8 — Autonomous + Collab"),
    "ops_replay": ("9", "🌐 Phase 9 — Distributed + Event Fabric"),
    "ops_health": ("10", "🛡️ Phase 10 — Reliability + Recovery"),
    "ops_outbox": ("10", "🛡️ Phase 10 — Reliability + Recovery"),
    "ops_topology": ("10", "🛡️ Phase 10 — Reliability + Recovery"),
}


def critique_form(key: str, label: str) -> str:
    safe = html.escape(label)
    return dedent(f"""
    <div class="bg-indigo-50 p-4 border-t border-slate-200" style="border-left:4px solid #6366f1;">
      <div class="flex items-center gap-2 mb-2">
        <span class="text-lg">💬</span>
        <strong class="text-slate-800 text-sm">Verdict on {safe}</strong>
      </div>
      <div class="flex flex-wrap gap-3 items-center mb-2 text-sm">
        <label class="flex items-center gap-1"><input type="radio" name="verdict-{key}" value="approved" class="text-emerald-600"> ✅ OK</label>
        <label class="flex items-center gap-1"><input type="radio" name="verdict-{key}" value="needs-work" class="text-rose-600"> 🔴 Needs work</label>
        <label class="flex items-center gap-1"><input type="radio" name="verdict-{key}" value="confusing" class="text-amber-600"> 🟡 Confusing</label>
        <label class="flex items-center gap-1"><input type="radio" name="verdict-{key}" value="cut-it" class="text-slate-600"> 🗑️ Cut it</label>
        <select name="rating-{key}" class="border border-slate-300 rounded px-2 py-1 text-xs ml-auto">
          <option value="">—</option><option value="5">⭐⭐⭐⭐⭐</option><option value="4">⭐⭐⭐⭐</option>
          <option value="3">⭐⭐⭐</option><option value="2">⭐⭐</option><option value="1">⭐</option>
        </select>
      </div>
      <textarea name="notes-{key}" rows="2"
                placeholder="Notes — what would you change?"
                class="w-full border border-slate-300 rounded p-2 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-400"></textarea>
    </div>""")


def page_card(item: dict) -> str:
    label = item["label"]
    slug = item["file"].replace("live_", "").replace(".png", "")
    url = item["url"]
    fname = item["file"]
    ok = item.get("ok", False)
    status = item.get("status", "—")
    err = item.get("error", "")
    phase_code, phase_label = PHASE_MAP.get(slug, ("?", "?"))
    short_url = url.replace("http://localhost:8765", "")

    if ok:
        img = f'<img src="screenshots/{fname}" class="w-full block">'
        status_html = f'<span class="text-emerald-600 text-xs font-bold">HTTP {status}</span>'
    else:
        img = (f'<div class="bg-rose-50 border-2 border-rose-200 p-12 text-center">'
                  f'<div class="text-5xl">🔴</div>'
                  f'<div class="font-bold text-rose-800 mt-2">Capture failed</div>'
                  f'<div class="text-xs text-rose-600 mt-1">{html.escape(err)}</div></div>')
        status_html = f'<span class="text-rose-600 text-xs font-bold">HTTP {status}</span>'

    return dedent(f"""
    <section id="page-{slug}" class="bg-white rounded-2xl shadow-sm border border-slate-200 mb-5 overflow-hidden">
      <div class="px-5 py-3 border-b border-slate-100 flex items-center justify-between bg-slate-50">
        <div>
          <div class="text-xs text-slate-500">{html.escape(phase_label)}</div>
          <div class="font-bold text-slate-800">{html.escape(label)}</div>
          <a href="{html.escape(url)}" target="_blank"
             class="text-xs text-indigo-600 hover:underline font-mono">{html.escape(short_url)} ↗</a>
        </div>
        <div>{status_html}</div>
      </div>
      <div class="border-b border-slate-100">{img}</div>
      {critique_form('page-' + slug, label)}
    </section>""")


def build():
    items = json.loads(INDEX.read_text(encoding="utf-8"))

    # Group by phase, preserving insertion order
    groups: dict[str, list[dict]] = {}
    for it in items:
        slug = it["file"].replace("live_", "").replace(".png", "")
        phase_code, phase_label = PHASE_MAP.get(slug, ("?", "Other"))
        groups.setdefault(phase_label, []).append(it)

    grouped_html = ""
    for label, page_items in groups.items():
        grouped_html += f'<h2 class="text-2xl font-bold mt-10 mb-4 text-slate-800">{html.escape(label)}</h2>\n'
        grouped_html += "\n".join(page_card(p) for p in page_items)

    # TOC
    toc = ""
    for it in items:
        slug = it["file"].replace("live_", "").replace(".png", "")
        toc += (f'<a href="#page-{slug}" class="block px-3 py-1 rounded hover:bg-indigo-50 '
                  f'hover:text-indigo-700 text-sm">{html.escape(it["label"])}</a>\n')

    ok_count = sum(1 for i in items if i.get("ok"))
    total = len(items)

    sections_js = json.dumps([
        ("page-" + it["file"].replace("live_", "").replace(".png", ""), it["label"])
        for it in items
    ])

    return dedent(f"""<!doctype html>
<html lang="en"><head>
  <meta charset="utf-8">
  <title>📸 Live UI Gallery — Phases 0–10</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}</style>
</head>
<body class="bg-slate-50">
  <div class="bg-gradient-to-br from-indigo-900 via-purple-900 to-slate-900 text-white py-10">
    <div class="max-w-7xl mx-auto px-6 text-center">
      <div class="text-6xl mb-3">📸 🖼️ ✨</div>
      <h1 class="text-4xl font-extrabold">Live UI Gallery</h1>
      <p class="text-xl text-slate-300 mt-2">The actual screens behind Phases 0–10 — captured from <code>http://localhost:8765</code></p>
      <p class="text-sm text-emerald-400 mt-3">📊 {ok_count}/{total} pages captured · 🔗 click any URL to open the live page</p>
    </div>
  </div>

  <div class="max-w-7xl mx-auto px-6 py-8 flex gap-8">
    <aside class="w-64 flex-none sticky top-6 self-start hidden lg:block">
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-3">
        <div class="font-bold text-slate-800 mb-2 px-2">📚 Pages</div>
        {toc}
        <div class="mt-3 pt-2 border-t border-slate-200">
          <a href="#critique" class="block bg-indigo-600 text-white text-center font-semibold py-2 rounded hover:bg-indigo-700">🎯 Generate Response</a>
          <a href="index.html" class="block mt-2 text-center text-xs text-slate-500 hover:text-indigo-700">↩ Back to System Tour</a>
        </div>
      </div>
    </aside>

    <main class="flex-1 min-w-0">

      <div class="bg-amber-50 border border-amber-200 rounded-xl p-4 mb-6 text-sm text-amber-900">
        <strong>📝 How to use this page:</strong>
        For each screenshot below, mark a verdict (✅ OK / 🔴 Needs work / 🟡 Confusing / 🗑️ Cut it) and leave notes.
        At the bottom, click <strong>🎯 Generate Response</strong> to compile your feedback into a message you can paste back to Claude.
        Click any URL to open the live page in a new tab and click around.
      </div>

      {grouped_html}

      <h2 id="critique" class="text-3xl font-bold mt-12 mb-4 flex items-center gap-3"><span>🎯</span>Overall Verdict</h2>
      <div class="bg-white rounded-2xl shadow-sm border border-slate-200 mb-6 p-6">
        <h3 class="font-semibold text-slate-800 mb-2">Anything else? 💭</h3>
        <textarea id="overall-notes" rows="6"
                  placeholder="Overall — what's broken, what's confusing, what should be unified, what should be cut..."
                  class="w-full border border-slate-300 rounded-md p-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"></textarea>
        <div class="mt-5 flex items-center gap-3">
          <button onclick="generateResponse()"
                  class="bg-indigo-600 hover:bg-indigo-700 text-white font-semibold px-6 py-3 rounded-lg shadow-md flex items-center gap-2 text-lg">
            <span>🎯</span> Generate Response for Claude
          </button>
        </div>
      </div>

    </main>
  </div>

  <div id="response-modal" class="fixed inset-0 bg-black/60 z-50 items-center justify-center p-6" style="display:none;">
    <div class="bg-white rounded-2xl shadow-2xl max-w-4xl w-full max-h-[85vh] flex flex-col">
      <div class="p-5 border-b border-slate-200 flex items-center justify-between">
        <h3 class="text-xl font-bold flex items-center gap-2"><span>📨</span>Paste this back to Claude</h3>
        <button onclick="closeModal()" class="text-slate-400 hover:text-slate-700 text-2xl leading-none">×</button>
      </div>
      <div class="p-5 flex-1 overflow-hidden flex flex-col">
        <textarea id="response-output" readonly
                  class="flex-1 border border-slate-300 rounded-lg p-4 font-mono text-sm bg-slate-50 resize-none"
                  style="min-height:300px;"></textarea>
        <div class="mt-4 flex gap-3">
          <button id="copy-btn" onclick="copyResponse()"
                  class="bg-emerald-600 hover:bg-emerald-700 text-white font-semibold px-5 py-2 rounded-lg flex items-center gap-2">
            <span>📋</span><span>Copy to Clipboard</span>
          </button>
          <button onclick="closeModal()" class="bg-slate-200 hover:bg-slate-300 px-5 py-2 rounded-lg">Close</button>
        </div>
      </div>
    </div>
  </div>

  <script>
    const SECTIONS = {sections_js};

    function collectSection(key, label) {{
      const v = document.querySelector('input[name="verdict-' + key + '"]:checked');
      const r = document.querySelector('select[name="rating-' + key + '"]');
      const n = document.querySelector('textarea[name="notes-' + key + '"]');
      return {{ key, label,
        verdict: v ? v.value : null,
        rating: r ? r.value : '',
        notes: n ? n.value.trim() : ''
      }};
    }}

    function generateResponse() {{
      const lines = ['# Live UI Review Response', '_Compiled from in-browser critique form._\\n', '## Per-page verdicts'];
      let any = false;
      SECTIONS.forEach(([key, label]) => {{
        const s = collectSection(key, label);
        if (s.verdict || s.rating || s.notes) {{
          any = true;
          lines.push('### ' + label);
          if (s.verdict) lines.push('- Verdict: **' + s.verdict + '**');
          if (s.rating) lines.push('- Rating: ' + s.rating + '/5');
          if (s.notes) lines.push('- Notes: ' + s.notes);
        }}
      }});
      if (!any) lines.push('_(No per-page input — see Overall.)_');
      const o = document.getElementById('overall-notes').value.trim();
      if (o) {{ lines.push('\\n## Overall feedback'); lines.push(o); }}
      lines.push('\\n---', 'Please address items marked **needs-work**, **cut-it**, and **confusing** first.');
      document.getElementById('response-output').value = lines.join('\\n');
      const m = document.getElementById('response-modal');
      m.style.display = 'flex';
      m.classList.remove('hidden');
    }}
    function closeModal() {{
      const m = document.getElementById('response-modal');
      m.style.display = 'none';
      m.classList.add('hidden');
    }}
    function copyResponse() {{
      const ta = document.getElementById('response-output');
      ta.select(); ta.setSelectionRange(0, 999999);
      try {{
        navigator.clipboard.writeText(ta.value).then(() => {{
          const btn = document.getElementById('copy-btn');
          btn.innerHTML = '<span>✅</span><span>Copied!</span>';
          setTimeout(() => btn.innerHTML = '<span>📋</span><span>Copy to Clipboard</span>', 2000);
        }});
      }} catch (e) {{ document.execCommand('copy'); }}
    }}
  </script>
</body></html>""")


def main():
    print("🔧 Building live UI gallery...")
    OUT.write_text(build(), encoding="utf-8")
    print(f"✅ Written: {OUT}")
    if sys.platform == "win32":
        os.startfile(str(OUT))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(OUT)])
    else:
        subprocess.run(["xdg-open", str(OUT)])
    print("🚀 Opened.")


if __name__ == "__main__":
    main()
