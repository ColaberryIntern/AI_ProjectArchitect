# Video 01 — Intro: What is the AI Project Architect & Build Companion?

**Runtime target:** 2:45–3:15
**Audience:** First-time viewers, all roles
**Goal:** In three minutes, the viewer understands what the product is, what it replaces, and what they'll do with it daily.

---

## Pre-production notes

- Record voiceover separately from screen capture; sync in editing.
- Tone: calm, conversational, slightly slower than normal speech. Pretend you're explaining it to a smart colleague over coffee.
- For abstract concepts (Scenes 1, 2, 6) use a simple title card or animated text — no screen recording needed.
- For product surface (Scenes 3–5) use real recordings of `advisor.colaberry.ai`.
- If a screen recording shows real Basecamp tasks or Gmail content, **blur or use a demo account** — don't ship real customer data in training material.

---

## Scene 1 — Cold open (0:00 – 0:15)

**SHOW:**
Title card on dark background, simple white text:
> **The AI Project Architect & Build Companion**
> *Your daily command center for project work.*

Hold for 3 seconds. Then dissolve to a static shot of `advisor.colaberry.ai` homepage.

**SAY:**
> "Every day, project work pulls you in three directions: emails to act on, tasks to triage, and reusable assets — prompts, agents, skills — scattered across a dozen places. This product brings all of that into one place."

---

## Scene 2 — The problem (0:15 – 0:45)

**SHOW:**
Simple animated diagram (or three side-by-side static images): a Gmail inbox icon, a Basecamp logo, and a folder icon labeled "Prompts & Skills." Arrows from each pointing at a frustrated stick-figure user in the center.

**SAY:**
> "If you're an operator at Colaberry, your work lives in three systems. Customer requests come in by email. Project tasks live in Basecamp. And the reusable building blocks you need — prompts, agent definitions, skills, MCP servers — live in a shared library. Switching between them costs you focus, and there's no single view of what actually needs your attention today."

---

## Scene 3 — The product, 30-second tour (0:45 – 1:30)

**SHOW:**
Open browser, navigate to `https://advisor.colaberry.ai/`. Pause on logged-in homepage for 2 seconds.

Then in sequence (each held ~5 seconds):
1. Click into `/my-day/` — show the task queue.
2. Click into `/library/` — show the asset categories.
3. Click into one asset detail page (any skill or use-case) — show the green Install panel.

**SAY:**
> "The product has two surfaces. **My Day** is your task command center — it pulls every task assigned to you from Basecamp, ranks them by urgency, and surfaces the ones that need a human decision. **The Library** is your shared catalog of reusable assets — skills, agents, prompts, MCP servers — that you can browse, install into your own workspace, and subscribe to for future updates. That's the whole product. Two pages."

---

## Scene 4 — What you do daily (1:30 – 2:15)

**SHOW:**
Return to `/my-day/`. Hover over (don't click) a task in the queue. Then scroll down to show the urgency tiers and filter controls.

Then quick cut to `/library/` with a search query typed in the search box (e.g., "playwright"). Show filtered results.

**SAY:**
> "Here's what daily use looks like. You open My Day in the morning. You see a ranked list of what needs you — assigned tasks, things coming due, customer requests pulled in from email. You work the top of the queue, you dismiss what's not yours, you click into the ones that need context. When you need a tool — a skill, an agent, a prompt — you go to the Library, find it, and install it into your workspace with one click. The system handles the wiring."

---

## Scene 5 — Why this works (2:15 – 2:45)

**SHOW:**
Split screen: left side shows a Basecamp todo with a comment that reads `via Ali's Claude Code`. Right side shows the same task in My Day with an "Open in Basecamp" link.

**SAY:**
> "Behind the scenes, the system is doing real work for you. Your tasks sync from Basecamp automatically. When you act on something through Claude Code, the work posts back to Basecamp with your name on it — your team sees what you did, not an anonymous bot. Email attachments get staged into Google Drive in the right folder. You don't manage the plumbing; you just do the work."

---

## Scene 6 — Close & next step (2:45 – 3:05)

**SHOW:**
Title card:
> **Next: Connect your Basecamp account (2 min)**
> *Video 04 in this series.*

**SAY:**
> "The next video walks you through the one-time setup: connecting your Basecamp account so My Day can see your tasks. It takes two minutes. After that, you're ready to use the product every day."

---

## Screenshot list (for the editor)

The editor will need these stills, captured at 1920×1080:

1. `advisor.colaberry.ai` logged-in homepage
2. `/my-day/` showing 5+ tasks in the queue
3. `/library/` category grid
4. A single asset detail page with green Install panel visible
5. `/my-day/` with filter sidebar open
6. `/library/` search results (use a generic query like "skill")
7. A Basecamp todo with `via [Name]'s Claude Code` comment visible
8. A My Day task detail page with "Open in Basecamp" button

Save all stills to `/directives/training/assets/screenshots/intro/`.

---

## Music & SFX

- **Bed music:** soft instrumental, no lyrics, -24 LUFS. Free options: YouTube Audio Library ("Reflections" or "Soft Inspiration").
- **SFX:** none — no transition swooshes, no click sounds. The product is a serious tool; the video should sound serious.

---

## Captions / SRT

Generate captions from the SAY column verbatim. Use a service like OBS's built-in caption export or `whisper.cpp` against the final audio track. Ship `.srt` alongside the `.mp4`.
