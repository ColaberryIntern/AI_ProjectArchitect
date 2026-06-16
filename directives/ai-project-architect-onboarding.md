# AI_ProjectArchitect — Employee Onboarding Runbook

**Audience:** Every Colaberry employee (and, in time, customer-company employees)
**Time:** 15 minutes for the runbook; revisit as you go.
**Ticket:** [Infra 3](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889172) · due 2026-06-16

---

## TL;DR — what this is for

advisor.colaberry.ai/library is your **personal AI workbench + the company's shared library**. It's where you:

1. **Find** the prompts, agents, skills, and MCP servers your colleagues have built and approved.
2. **Use** them (every Library item shows you exactly how to install + invoke it).
3. **Build** your own and submit them for approval — so the next person doesn't have to re-invent.
4. **Get credit** for what you ship (every item shows the author + ratings + which companies have approved it).

If you're technical, you'll also get a **personal GitHub workspace** under `ColaberryIntern/{yourname}-workspace` with your `.claude/skills/` scaffold and a credentials vault for your tool tokens.

---

## Part 1 — Access (everyone)

### Step 1.1 — Sign in
1. Open https://advisor.colaberry.ai/library
2. Click "🔑 Sign in" in the top-right
3. Sign in with your colaberry.com Google account
4. First sign-in auto-provisions your user record. You'll land back on the library home.

> **What you should see after login:** your name + "colaberry" tenant chip in the header, the scope switcher set to "🏢 My company", and a counter badge on 🔔 if anyone has notified you (likely empty on first visit).

### Step 1.2 — Verify your profile
1. Click your name in the top-right → "👤 My profile"
2. Confirm your display name + email + role are right
3. If you need additional roles (admin, contributor), ping `#ai-platform` — Ali or another admin grants via /admin/users/{your_id}

### Step 1.3 — Tour the scopes
The scope switcher in the header has three modes:
- **🌍 All** — every item visible to the entire authenticated library (open mode)
- **🏢 My company** — items your tenant owns + items others have approved as shared-public + items in your tenant's approved set
- **👤 Mine** — only items you personally submitted

Default for logged-in users is **My company**. Click around to feel the difference.

---

## Part 2 — Find + use (everyone)

### Step 2.1 — Browse the catalogue
The library has 6 categories. From any page, the left nav shows: 📋 Use Cases · 💡 Prompts · 🤖 Agents · 🛠 Skills · 🔌 MCP Servers · 🎯 Capabilities. Click any to see what's there.

### Step 2.2 — Search + filter
On every category page:
- **Tag word cloud** at the top — click any tag to narrow
- **Filter chips:** "All", "✓ Colaberry vetted only", "✓ {your company} approved" (when logged in), "Other company approved ▾" (cross-tenant)
- **Sort** by rating, recency, or alphabetical (top-right of the inventory table)

### Step 2.3 — Use an item
Click any item to see its detail page. Every item has a concrete next-step action:
- **🌐 View source** — link to the canonical repo / docs
- **📋 Copy install** — if it's installable (most MCP servers + skills), one-click clipboard copy of the install command
- **⬇️ Install page / 📖 Docs / 🏠 Homepage** — direct links when published

For skills installed in your `.claude/skills/` workspace, the item page shows the slash-invocation: e.g. `/karun-dash` for the karun-agent dashboard skill.

### Step 2.4 — Rate + comment
Every item has a 5-star rating and a discussion thread at the bottom. Both are public to your tenant. Use them — the system rolls up ratings into the "vetted-by-usage" signal that drives the top-of-page sorting.

---

## Part 3 — Submit + improve (everyone)

### Step 3.1 — Submit a new item
Two paths:

**A. URL ingest** (you have a public GitHub repo / docs URL):
- Top-right "🌐 Ingest URL" button
- Paste URL, pick a category, click submit
- The scanner builds the metadata from the URL automatically

**B. Manual submit** (you've built something internally):
- Top-right "➕ Submit manually" button
- Fill name, kind, description, tags, source URL (your workspace repo or wherever it lives), install command
- Click submit

Either way, the item enters **draft → submitted** and lands in your tenant's moderation queue at `/admin/{your_company}/queue`.

### Step 3.2 — What happens after submit
Per [Workflow 1]:

1. **Submitted** — visible in your company's queue. A bell-counter pings any admin in your tenant.
2. **Under review** — an admin (currently Ali for Colaberry; per-customer reviewer for other tenants) claims it.
3. **Decided** — three outcomes:
   - **Approved** → item shows up in your tenant's library, badged "✓ {Company} approved", and (for Colaberry-approved items) syncs to the canonical GitHub repo via [Infra 2] sync PR
   - **Changes requested** → comment thread back to you with what needs fixing; revise and resubmit
   - **Rejected** → archived with the rejection reason

You'll get a notification in your 🔔 bell + a daily digest email (we batch — no per-event spam).

### Step 3.3 — Etiquette in the global feed

- **Be specific in the description.** "Helpful prompt for sales" gets rejected. "Prompt that generates a 4-question discovery doc from a LinkedIn profile" gets approved.
- **Include a measurable ROI claim where possible.** "Saves ~20 min per prospect" is useful. "Saves time" is not.
- **Don't submit credentials.** The smoke-test gate ([Infra 2]) scans for ghp_ tokens, AWS keys, etc. and blocks the sync. Store credentials in the vault via /admin/users/{you}/scopes instead.
- **Don't duplicate.** Search first. If you find something close, comment/rate on the existing item rather than submitting a fork. If you genuinely improve it, submit as a new version with a "v2 of {original}" note.
- **Pick the right category.** Use cases describe a business outcome. Skills/agents/prompts are the implementations. MCP servers are tool connectors. Capabilities are deeper abstractions. When in doubt, ask in `#ai-platform`.

---

## Part 4 — Personas

### 4a. Non-technical contributor (most of the team)

You'll mostly **use** the library, **rate + comment** on what helps, and occasionally **submit a use case** describing a problem you've solved (even if the implementation is by a teammate). You don't need a GitHub workspace, a vault, or any local tooling. Everything happens at advisor.colaberry.ai in the browser.

**Your one daily habit:** when you successfully use a Library item, give it a 1-line comment + a star rating. The system gets dramatically better when ~10 people do this per day.

### 4b. Technical contributor (engineers, data scientists)

In addition to the above, you can build and contribute your own assets (skills, agents, prompts, MCP servers). **You do NOT need to clone any repo or have a GitHub account.** Your Colaberry context — the operating doctrine, your Basecamp tools, and your memory — reaches Claude Code through the MCP you connect once at [/profile/welcome](https://advisor.colaberry.ai/profile/welcome). (A per-person `{yourname}-workspace` repo exists, but it's a behind-the-scenes sync artifact the system manages; you never touch it.)

Workflow:
1. Open Claude Code in any folder on your machine, with the Colaberry MCP connected.
2. Write a skill (a Markdown `SKILL.md`), agent, or prompt — see existing library items for the format — and test it.
3. Add it to the company library by just telling Claude **"add this to our library."** Claude files it via `colaberry_propose_asset` (it picks a category, names it, and tags it to you), and it shows up at `/library/<category>/<id>`. For an asset that already lives in a public repo, use the library's **Ingest URL** flow and paste that repo's URL instead.

When admin approves your asset, [Infra 2] syncs it to the canonical `ColaberryIntern/AI_ProjectArchitect` repo at `library/skills/{name}.md`. From that moment it's available to every colleague's `/library/skills/{name}` and (if `shared-public`) to other tenants.

### 4c. Admin / reviewer (Ali for Colaberry; per-tenant admins for others)

You see two extra things in the header:
- **📥 {count}** — items in your tenant's moderation queue. Click → `/admin/{your_company}/queue`
- **🔔 {count}** — unread notifications (submissions to review + decision pings on items where you were the original author)

Your daily habit: walk the queue. Claim → review → decide. Per [Infra 4], you'll also have a weekly 30-min cadence with Ali on rubric calibration if you're a pilot DRI.

---

## Part 5 — What does NOT change

This is worth reading twice (per Ram's all-hands message):

- This is not surveillance. There's no individual-keystroke logging.
- This is not a scoring tool for individuals. We measure the system, not you.
- This is not a job-replacement initiative. We are not deciding who gets cut; we are deciding what gets shipped to customers.
- Your existing tools (Notion, Slack, Jira, your IDE) keep working unchanged. The Library complements them; it does not replace them.

---

## Help + escalations

- **Slack `#ai-platform`** — fastest path; Ali, Karun, Kes, and Sohail are watching.
- **Feedback form** — advisor.colaberry.ai/library/feedback (anonymous OK).
- **Bug reports** — open a Basecamp todo in the AI_ProjectArchitect project + tag Ali.
- **Security concerns** — DM Ali directly. Do not paste credentials anywhere in the Library, even as examples.

---

## Companion artifacts

- `directives/comms-ram-all-hands.md` — what Ram sent on rollout day
- `directives/pilot-weekly-cadence.md` — Ali ⇄ DRI weekly format (relevant if you're a pilot)
- `docs/specs/library-01-approval-filter.md` + `library-02-identity-switcher.md` — what the per-tenant filtering does under the hood
- `docs/specs/workflow-01-publish-queue.md` — the state machine your submissions traverse
- `docs/specs/infra-02-pr-based-github-sync.md` — what happens when your item is approved (the GitHub PR + CI gate flow)

---

## Companion screencast (TODO)

A 5-minute screencast walkthrough is planned for the v2 of this runbook (per the original [Infra 3] deliverable). Estimated record date: 2026-06-15 once Auth 2 is live in prod. Until then, this written runbook is the canonical onboarding.
