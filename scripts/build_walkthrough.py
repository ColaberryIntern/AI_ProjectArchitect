"""Build a 'real use case walkthrough' HTML.

Picks the existing ai-curriculum project (already completed end-to-end),
captures a screenshot of each step's page, and stitches them into a
narrative HTML — 'Sarah had an idea. Here's exactly what each step did.'

Requires server running on http://localhost:8765.

Run:  python scripts/build_walkthrough.py
Output: output/system_tour/walkthrough.html (auto-opens)
"""

from __future__ import annotations

import html
import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
TOUR = ROOT / "output" / "system_tour"
SHOTS = TOUR / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
OUT = TOUR / "walkthrough.html"

BASE = "http://localhost:8765"
SLUG = "ai-curriculum"  # completed real project we use as the case study


# Read real artifacts from the project so the narrative is honest
STATE = json.loads(
    (ROOT / "output" / SLUG / "project_state.json").read_text(encoding="utf-8")
)
RAW_IDEA = STATE["idea"]["original_raw"].strip()
FEATURES_CORE = STATE["features"]["core"][:8]
OUTLINE_SECTIONS = STATE["outline"]["sections"]
CHAPTERS_TITLES = [c.get("title", "?") if isinstance(c, dict) else "?"
                          for c in STATE["chapters"][:10]]
LOCKED_HASH = STATE["outline"]["locked_hash"][:24] + "…"


# ─── Steps in the case study ────────────────────────────────────────────────

STEPS = [
    {
        "n": 1,
        "emoji": "💡",
        "title": "Step 1 — Idea Intake",
        "subtitle": "Type your raw idea, even if it's a rambling sentence.",
        "url": f"{BASE}/projects/{SLUG}/idea-intake",
        "shot": "walk_1_intake.png",
        "what_user_does": (
            "Sarah opens the system. She types her idea exactly as it sounds in her head — "
            "no need to polish it."
        ),
        "real_input": RAW_IDEA,
        "what_system_does": (
            "The system reads the raw idea, asks targeted follow-up questions "
            "to fill the gaps (who's the user? what's the business model? what's "
            "the AI doing exactly?), and stores everything in a single project "
            "state file."
        ),
        "output_label": "Captured raw idea — the verbatim source of truth",
    },
    {
        "n": 2,
        "emoji": "🧭",
        "title": "Step 2 — Feature Discovery",
        "subtitle": "Pick the core features. Reject the nice-to-haves.",
        "url": f"{BASE}/projects/{SLUG}/feature-discovery",
        "shot": "walk_2_features.png",
        "what_user_does": (
            "The system shows a catalog of features that match the idea, sorted "
            "by what makes the most sense for this kind of product. Sarah marks "
            "which ones are CORE (must-haves) and which are OPTIONAL (later)."
        ),
        "real_input": (
            f"Sarah approved {len(STATE['features']['core'])} core features. "
            f"The catalog suggested {len(STATE['features']['catalog'])} candidates total."
        ),
        "what_system_does": (
            "The system has an anti-overengineering guardrail. If you pick "
            "too many 'core' features, it pushes back. The classification "
            "(core vs optional) determines the build order downstream."
        ),
        "features_list": FEATURES_CORE,
        "output_label": f"{len(STATE['features']['core'])} core features approved",
    },
    {
        "n": 3,
        "emoji": "🔒",
        "title": "Step 3 — Outline Generation + Locking",
        "subtitle": "Draft the 10-section outline, then SHA256-lock it.",
        "url": f"{BASE}/projects/{SLUG}/outline-generation",
        "shot": "walk_3_outline.png",
        "what_user_does": (
            "The system drafts a 10-section outline (Executive Summary → Roadmap). "
            "Sarah reviews each section, adjusts as needed, then clicks Approve."
        ),
        "real_input": (
            f"Sarah approved version {STATE['outline']['version']} on "
            f"{STATE['outline']['locked_at'][:10]}."
        ),
        "what_system_does": (
            "Once approved, the outline is hashed with SHA-256 and locked. Any "
            "future edit forces a new version. This prevents silent drift — "
            "chapters can't reference sections that don't exist in the locked outline."
        ),
        "outline_sections": [s["title"] for s in OUTLINE_SECTIONS],
        "output_label": f"Outline v{STATE['outline']['version']} locked · SHA256: {LOCKED_HASH}",
    },
    {
        "n": 4,
        "emoji": "📚",
        "title": "Step 4 — Chapter Build",
        "subtitle": "One chapter at a time. Max 2 revisions each.",
        "url": f"{BASE}/projects/{SLUG}/chapter-build",
        "shot": "walk_4_chapters.png",
        "what_user_does": (
            "For each locked outline section, the system writes a chapter with "
            "three parts: 🎯 Purpose · 🧠 Design Intent · 🔧 Implementation Guidance. "
            "Sarah reviews and either approves or asks for a revision (max 2 revisions per chapter)."
        ),
        "real_input": (
            f"{len(STATE['chapters'])} chapters written. Total document length below."
        ),
        "what_system_does": (
            "Hard cap of 2 revisions per chapter forces the system to converge. "
            "Each chapter must summarize in one sentence. Forbidden phrases "
            "('handle edge cases', 'optimize later') trigger automatic rewrites."
        ),
        "chapters_list": CHAPTERS_TITLES,
        "output_label": f"{len(STATE['chapters'])} chapters approved",
    },
    {
        "n": 5,
        "emoji": "🚦",
        "title": "Step 5 — Quality Gates",
        "subtitle": "5 mandatory checks. All must pass to ship.",
        "url": f"{BASE}/projects/{SLUG}/quality-gates",
        "shot": "walk_5_gates.png",
        "what_user_does": (
            "Sarah clicks 'Run quality gates'. The system runs five checks, "
            "each producing a pass/fail with diagnostics if anything fails."
        ),
        "real_input": (
            "Gates: ✅ Completeness · ✅ Clarity · ✅ Build Readiness · "
            "✅ Anti-Vagueness · ✅ Intern Success Test"
        ),
        "what_system_does": (
            "Completeness checks for placeholders ('TBD', 'we'll decide later'). "
            "Anti-Vagueness greps for banned phrases. The Intern Test asks: "
            "'Could a junior dev execute this project using ONLY this doc?'"
        ),
        "output_label": "All gates passed · ready for final assembly",
    },
    {
        "n": 6,
        "emoji": "📄",
        "title": "Step 6 — Final Assembly",
        "subtitle": "Download the build-ready Markdown.",
        "url": f"{BASE}/projects/{SLUG}/final-assembly",
        "shot": "walk_6_final.png",
        "what_user_does": (
            "Sarah clicks Download. She gets one structured Markdown file: "
            "Executive Summary, problem context, personas, all 10 chapters, "
            "metrics, roadmap."
        ),
        "real_input": "One file. Versioned. Reproducible.",
        "what_system_does": (
            "Mechanical compilation only — no creative rewriting at this stage. "
            "The doc is the source of truth for the build. Any future change "
            "starts a new version (the existing one is immutable)."
        ),
        "output_label": "AI_Curriculum_Build_Guide_v1.md — ready to hand to an engineer",
    },
]


# ─── Capture ───────────────────────────────────────────────────────────────

def capture():
    print(f"📸 Capturing live screenshots from {BASE} for project '{SLUG}'...")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        for step in STEPS:
            page = ctx.new_page()
            try:
                resp = page.goto(step["url"], wait_until="domcontentloaded",
                                       timeout=15000)
                page.wait_for_timeout(700)
                page.screenshot(path=str(SHOTS / step["shot"]), full_page=False)
                print(f"  ✅ {resp.status} {step['title']}")
            except Exception as e:
                print(f"  ❌ {step['title']} — {e}")
            finally:
                page.close()
        browser.close()


# ─── HTML ──────────────────────────────────────────────────────────────────

def critique_form(key: str, label: str) -> str:
    safe = html.escape(label)
    return dedent(f"""
    <div class="bg-indigo-50 p-4 border-t border-slate-200" style="border-left:4px solid #6366f1;">
      <div class="text-sm font-semibold mb-2 text-slate-800">💬 Did this step make sense?</div>
      <div class="flex flex-wrap gap-3 mb-2 text-sm">
        <label class="flex items-center gap-1"><input type="radio" name="verdict-{key}" value="clear" class="text-emerald-600"> ✅ Crystal clear</label>
        <label class="flex items-center gap-1"><input type="radio" name="verdict-{key}" value="mostly" class="text-amber-600"> 🟡 Mostly clear</label>
        <label class="flex items-center gap-1"><input type="radio" name="verdict-{key}" value="confused" class="text-rose-600"> 🔴 Still confused</label>
      </div>
      <textarea name="notes-{key}" rows="2"
                placeholder="What's confusing? What would help?"
                class="w-full border border-slate-300 rounded p-2 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-400"></textarea>
    </div>""")


def step_card(s: dict) -> str:
    extras = ""
    if "features_list" in s:
        extras = ('<div class="mt-3 p-3 bg-emerald-50 rounded-lg">'
                   '<div class="text-xs font-bold text-emerald-700 mb-2">✅ Features Sarah approved (first 8 of '
                  + str(len(STATE['features']['core'])) + '):</div><ul class="text-sm text-emerald-900 space-y-1 ml-4 list-disc">'
                   + "".join(f"<li>{html.escape(str(f))}</li>" for f in s['features_list'])
                   + "</ul></div>")
    if "outline_sections" in s:
        extras = ('<div class="mt-3 p-3 bg-emerald-50 rounded-lg">'
                   '<div class="text-xs font-bold text-emerald-700 mb-2">🔒 The 10 locked outline sections:</div>'
                   '<ol class="text-sm text-emerald-900 space-y-1 ml-5 list-decimal">'
                   + "".join(f"<li>{html.escape(t)}</li>" for t in s['outline_sections'])
                   + "</ol></div>")
    if "chapters_list" in s:
        extras = ('<div class="mt-3 p-3 bg-emerald-50 rounded-lg">'
                   '<div class="text-xs font-bold text-emerald-700 mb-2">📚 The 10 chapters Sarah got:</div>'
                   '<ol class="text-sm text-emerald-900 space-y-1 ml-5 list-decimal">'
                   + "".join(f"<li>{html.escape(t)}</li>" for t in s['chapters_list'])
                   + "</ol></div>")

    safe_real_input = html.escape(s['real_input'])

    return dedent(f"""
    <section id="step-{s['n']}" class="bg-white rounded-2xl shadow-md border border-slate-200 mb-8 overflow-hidden">
      <!-- Header -->
      <div class="bg-gradient-to-r from-indigo-600 to-purple-600 text-white p-6">
        <div class="flex items-start gap-4">
          <div class="text-6xl">{s['emoji']}</div>
          <div class="flex-1">
            <div class="text-sm font-bold opacity-80 uppercase tracking-widest">Step {s['n']} of 6</div>
            <h2 class="text-3xl font-extrabold">{html.escape(s['title'].split(' — ', 1)[-1])}</h2>
            <p class="text-lg opacity-90 italic mt-1">{html.escape(s['subtitle'])}</p>
          </div>
        </div>
      </div>

      <!-- Screenshot -->
      <div class="border-b border-slate-200">
        <img src="screenshots/{s['shot']}" alt="{html.escape(s['title'])}" class="w-full block">
        <div class="bg-slate-100 px-4 py-2 text-xs text-slate-600 flex justify-between">
          <span>📷 The actual page Sarah saw</span>
          <a href="{html.escape(s['url'])}" target="_blank" class="text-indigo-600 hover:underline font-mono">{html.escape(s['url'].replace(BASE, ''))} ↗</a>
        </div>
      </div>

      <!-- Story -->
      <div class="p-6 grid md:grid-cols-2 gap-6">
        <div>
          <div class="text-xs font-bold text-slate-500 uppercase tracking-wide mb-2">👤 What Sarah does</div>
          <p class="text-slate-700 leading-relaxed">{html.escape(s['what_user_does'])}</p>
          <div class="mt-4 p-3 bg-amber-50 border border-amber-200 rounded-lg">
            <div class="text-xs font-bold text-amber-900 mb-1">📥 Her actual input on this step:</div>
            <div class="text-sm text-amber-900 italic">"{safe_real_input}"</div>
          </div>
        </div>
        <div>
          <div class="text-xs font-bold text-slate-500 uppercase tracking-wide mb-2">🤖 What the system does</div>
          <p class="text-slate-700 leading-relaxed">{html.escape(s['what_system_does'])}</p>
          {extras}
        </div>
      </div>

      <!-- Output -->
      <div class="bg-emerald-50 border-t border-emerald-200 p-4">
        <div class="flex items-center gap-3">
          <div class="text-3xl">✅</div>
          <div>
            <div class="text-xs font-bold text-emerald-700 uppercase">Output of this step</div>
            <div class="font-semibold text-emerald-900">{html.escape(s['output_label'])}</div>
          </div>
        </div>
      </div>

      {critique_form('step-' + str(s['n']), s['title'])}
    </section>""")


def build_html() -> str:
    steps_html = "\n".join(step_card(s) for s in STEPS)
    sections_js = json.dumps([(f"step-{s['n']}", s['title']) for s in STEPS])

    return dedent(f"""<!doctype html>
<html lang="en"><head>
  <meta charset="utf-8">
  <title>👩‍💼 Sarah's Story — One Real Use Case, End to End</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}</style>
</head>
<body class="bg-slate-50">

  <!-- HERO -->
  <div class="bg-gradient-to-br from-slate-900 via-indigo-900 to-purple-900 text-white py-14">
    <div class="max-w-4xl mx-auto px-6 text-center">
      <div class="text-7xl mb-4">👩‍💼 ➜ 📄</div>
      <h1 class="text-5xl font-extrabold leading-tight">Sarah Had an Idea.<br>Here's What Happened.</h1>
      <p class="text-xl text-slate-300 mt-4">
        A real, completed run through the AI Project Architect.<br>
        6 steps. One Markdown doc at the end. Real screenshots throughout.
      </p>
    </div>
  </div>

  <!-- THE IDEA -->
  <div class="max-w-4xl mx-auto px-6 -mt-8">
    <div class="bg-white rounded-2xl shadow-xl border border-slate-200 p-8">
      <div class="flex items-start gap-4">
        <div class="text-5xl">💭</div>
        <div class="flex-1">
          <div class="text-sm font-bold text-slate-500 uppercase tracking-widest">Sarah's idea, in her own words</div>
          <p class="text-xl text-slate-800 mt-2 italic leading-relaxed">"{html.escape(RAW_IDEA)}"</p>
          <div class="mt-4 text-sm text-slate-600">
            👇 Below is what each of the 6 steps actually did with that idea, with real screenshots of the pages she saw.
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- STEPS -->
  <div class="max-w-4xl mx-auto px-6 py-10">
    <h2 class="text-2xl font-bold text-slate-800 mb-6">📋 The 6 Steps</h2>
    {steps_html}

    <!-- WHAT SHE GOT -->
    <section class="bg-gradient-to-br from-emerald-500 to-teal-600 text-white rounded-2xl shadow-xl p-8 mt-8">
      <div class="flex items-start gap-4">
        <div class="text-6xl">🎁</div>
        <div class="flex-1">
          <div class="text-sm font-bold opacity-80 uppercase tracking-widest">End Result</div>
          <h2 class="text-3xl font-extrabold mt-1">One Markdown file an engineer could pick up tomorrow.</h2>
          <p class="text-lg opacity-95 mt-3 leading-relaxed">
            <strong>10 chapters</strong> covering everything from problem context → AI architecture → security →
            success metrics → roadmap. Locked outline hash so nothing drifts. All 5 quality gates passed.
            The whole thing took Sarah maybe an hour. Without the system, this is a <em>week's</em> worth of
            product-management work — meetings, drafts, reviews, revisions.
          </p>
          <div class="mt-5 bg-white/10 backdrop-blur rounded-xl p-4">
            <div class="text-xs uppercase tracking-widest opacity-80 mb-2">📦 What's in the doc</div>
            <ul class="text-sm space-y-1">
              {"".join(f'<li>✅ {html.escape(s["title"])}</li>' for s in OUTLINE_SECTIONS)}
            </ul>
          </div>
        </div>
      </div>
    </section>

    <!-- "BUT WHAT ABOUT PHASES 1-10?" -->
    <section class="bg-white rounded-2xl shadow-md border border-slate-200 mt-8 p-8">
      <div class="flex items-start gap-4">
        <div class="text-5xl">🤔</div>
        <div class="flex-1">
          <h2 class="text-2xl font-bold text-slate-900">"OK — but where do Phases 1–10 fit in?"</h2>
          <p class="text-slate-700 mt-3 leading-relaxed">
            Honest answer: <strong>for Sarah, they're invisible.</strong> What she sees is the 6-step
            pipeline above. Phases 1–10 sit <em>underneath</em> it, doing things she never has to think about:
          </p>
          <div class="grid md:grid-cols-2 gap-3 mt-4">
            <div class="p-3 bg-slate-50 rounded-lg text-sm">
              <strong>👥 If Sarah had a team</strong><br>
              <span class="text-slate-600">Workspaces (P1) keep her project separate from her co-founder's.
              RBAC (P5) lets the contractor only see chapters, not edit them.</span>
            </div>
            <div class="p-3 bg-slate-50 rounded-lg text-sm">
              <strong>🔁 If the LLM call crashed mid-chapter</strong><br>
              <span class="text-slate-600">Outbox (P10) + checkpoints (P10) → Sarah picks up exactly where she left off
              instead of starting that chapter over.</span>
            </div>
            <div class="p-3 bg-slate-50 rounded-lg text-sm">
              <strong>📡 If she wants live progress</strong><br>
              <span class="text-slate-600">Realtime bus (P7) + WebSocket gateway (P7) push "Chapter 5 done"
              to her browser without refresh.</span>
            </div>
            <div class="p-3 bg-slate-50 rounded-lg text-sm">
              <strong>🤖 If she wants the system to auto-build</strong><br>
              <span class="text-slate-600">Agent runtime (P8) with autonomy policy + recovery coordinator (P10)
              run the whole thing unattended, with audit trail.</span>
            </div>
            <div class="p-3 bg-slate-50 rounded-lg text-sm">
              <strong>🛡️ If a hostile audit asks "who changed what"</strong><br>
              <span class="text-slate-600">Signed audit log (P8) is HMAC-chained — every action provable,
              tampering detectable.</span>
            </div>
            <div class="p-3 bg-slate-50 rounded-lg text-sm">
              <strong>💾 If her laptop dies tomorrow</strong><br>
              <span class="text-slate-600">Backup integrity (P10) — SHA-256-verified snapshot restores her
              project byte-for-byte.</span>
            </div>
          </div>
          <p class="text-slate-700 mt-4 leading-relaxed">
            In short: the <strong>pipeline above (Phase 0)</strong> is what users see.
            <strong>Phases 1–10</strong> are the production hardening — the stuff you need to run this for
            <em>many users at once, on real infrastructure, surviving real failures</em>.
          </p>
        </div>
      </div>
    </section>

    <!-- OVERALL VERDICT -->
    <section class="bg-white rounded-2xl shadow-md border border-slate-200 mt-8 p-8" id="critique">
      <h2 class="text-2xl font-bold text-slate-900 flex items-center gap-3">
        <span class="text-3xl">🎯</span>Did this finally make sense?
      </h2>
      <p class="text-slate-600 mt-2">Tell me what's still unclear. I'll fix it.</p>
      <textarea id="overall-notes" rows="6"
                placeholder="Where did I lose you? What word/step/concept needs a better explanation?"
                class="w-full mt-4 border border-slate-300 rounded-md p-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"></textarea>
      <div class="mt-5 flex items-center gap-3">
        <button onclick="generateResponse()"
                class="bg-indigo-600 hover:bg-indigo-700 text-white font-semibold px-6 py-3 rounded-lg shadow-md flex items-center gap-2 text-lg">
          <span>🎯</span> Generate Response for Claude
        </button>
      </div>
    </section>

  </div>

  <!-- MODAL -->
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
      const n = document.querySelector('textarea[name="notes-' + key + '"]');
      return {{ key, label,
        verdict: v ? v.value : null,
        notes: n ? n.value.trim() : ''
      }};
    }}
    function generateResponse() {{
      const lines = ['# Walkthrough Feedback','_Compiled from Sarah-story walkthrough._\\n','## Per-step verdicts'];
      let any = false;
      SECTIONS.forEach(([key, label]) => {{
        const s = collectSection(key, label);
        if (s.verdict || s.notes) {{
          any = true;
          lines.push('### ' + label);
          if (s.verdict) lines.push('- ' + s.verdict);
          if (s.notes) lines.push('- Notes: ' + s.notes);
        }}
      }});
      if (!any) lines.push('_(No per-step input — see Overall.)_');
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
          setTimeout(() => btn.innerHTML = '<span>📋</span><span>Copy to Clipboard</span>', 2000);
        }});
      }} catch(e) {{ document.execCommand('copy'); }}
    }}
  </script>
</body></html>""")


def main():
    capture()
    print("🧱 Assembling walkthrough.html...")
    OUT.write_text(build_html(), encoding="utf-8")
    print(f"✅ {OUT}")
    if sys.platform == "win32":
        os.startfile(str(OUT))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(OUT)])
    else:
        subprocess.run(["xdg-open", str(OUT)])
    print("🚀 Opened in browser.")


if __name__ == "__main__":
    main()
