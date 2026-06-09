# Video 02 — Library Walkthrough: Find, Install, Subscribe

**Runtime target:** 4:45–5:15
**Audience:** Anyone who needs to find and reuse a skill, agent, prompt, MCP server, or use-case
**Prerequisites:** Viewer has logged into `advisor.colaberry.ai` at least once

---

## Pre-production notes

- Record this in **one continuous screen capture** if possible, then trim. The Library has a coherent flow; cuts between routes break that.
- Demo account note: use a Library asset that exists in the live catalog and won't be deleted. Recommended: pick a skill like `verify` or an agent like `code-reviewer` — both are stable.
- For the install demo, point your workspace_repo at a sandbox repo so the PR that opens during recording isn't a real one.

---

## Scene 1 — What the Library is (0:00 – 0:30)

**SHOW:**
Navigate to `https://advisor.colaberry.ai/library/`. Pause for 3 seconds on the category grid. Slowly scroll once down the page so the viewer sees the categories: skills, agents, prompts, MCP servers, use-cases, capabilities.

**SAY:**
> "The Library is Colaberry's shared catalog of reusable building blocks. There are six categories: skills, agents, prompts, MCP servers, use-cases, and capabilities. Every asset here has been contributed by someone on the team, gone through review, and is ready for you to use in your own work."

---

## Scene 2 — Browse by category (0:30 – 1:15)

**SHOW:**
Click into the **Skills** category. Show the list view — name, short description, rating, last updated.

Click into one specific skill (pick a stable one — e.g., `verify`). Pause on the detail page.

Point cursor sequentially at: title → description → "How to use" section → example → ratings → comments at the bottom.

**SAY:**
> "Click into any category to browse. Each row shows the name, what the asset does, its rating from other users, and when it was last updated. Click into an asset and you get the full detail page: a description, exactly how to use it, an example, ratings from other users, and a comments section where you can ask questions or share what worked. This is where you decide if an asset is right for your task."

---

## Scene 3 — Search and filter (1:15 – 1:45)

**SHOW:**
Click back to `/library/`. In the search bar at the top, type a query — e.g., "playwright" or "review." Show results.

Then click a filter chip (company filter or vetting status). Show the filtered narrower result set.

**SAY:**
> "If you know what you're looking for, search by keyword across all categories. You can also filter by company — to see only assets your company has access to — or by vetting status, to see only assets that have been formally reviewed. Use these filters when the catalog gets large; right now you can probably just browse."

---

## Scene 4 — The Install panel (1:45 – 2:45)

**SHOW:**
Return to the asset detail page from Scene 2. Scroll down to the **green Install panel** ("📥 Install to your workspace"). Pause for 3 seconds so the viewer can read it.

Point cursor at:
1. The "Install" button
2. The "Subscribe to updates" checkbox
3. The target repo text (which shows the user's workspace_repo)

**SAY:**
> "When you've found an asset you want to use, scroll to the green Install panel near the bottom of the page. This is the heart of the Library. Click Install, and the system opens a pull request in your workspace repository with this asset's files written into the right places. You don't have to know where skills live, or where agents go — the system knows. Check the 'Subscribe to updates' box, and any time this asset gets a new approved version, the system opens a follow-up pull request so you stay current."

---

## Scene 5 — Live install demo (2:45 – 3:45)

**SHOW:**
Check the "Subscribe to updates" checkbox. Click **Install**. Wait for the response (a few seconds).

The page should show a success state with a link to the opened PR. Click the PR link — it opens GitHub in a new tab. Show the PR title, the files changed tab, and the diff briefly.

Switch back to the Library tab.

**SAY:**
> "Watch what happens when I click Install. The system bundles the asset, figures out its dependencies — if a skill depends on a specific agent, that agent comes along too — writes them to the right paths in your repo, and opens a pull request. Here's the PR in GitHub. You can see the files it added. Review it like you'd review any pull request, merge when you're ready, and the asset is live in your workspace. Total elapsed time: about ten seconds."

---

## Scene 6 — When you can't install (3:45 – 4:15)

**SHOW:**
Navigate to an MCP server asset (one that's live-in-MCP). Scroll to where the Install panel would be — show that **it's not there**.

Briefly show the explanation text or use a title card overlay:
> *Live-in-MCP assets are already available to you via Claude's MCP.
> No install needed.*

**SAY:**
> "One thing to know: some assets — the ones already running as live MCP servers in your Claude session — don't have an Install button. That's intentional. They're already available to you; installing them again would risk leaking tokens. If you don't see the green panel, you don't need to do anything. Just use the asset from Claude directly."

---

## Scene 7 — Rating and contributing (4:15 – 4:45)

**SHOW:**
Back on a normal asset detail page. Click the star rating control — give it a rating. Type a short comment in the comment box (e.g., "Used this for X, worked well"). Submit.

Then navigate to `/library/ingest`. Show the URL submission form briefly.

**SAY:**
> "After you've used an asset, come back and rate it. Your rating helps everyone else on the team. Drop a comment about what you used it for — that context is more valuable than the star rating itself. And if you have something to contribute, the Ingest page lets you submit a GitHub URL or a docs link. The system scans it, fills in the metadata, and queues it for review. That's how the Library grows."

---

## Scene 8 — Close (4:45 – 5:00)

**SHOW:**
Title card:
> **Next: Your daily task workflow in My Day**
> *Video 03 in this series.*

**SAY:**
> "Now that you can find what you need, let's look at where you'll actually spend most of your day: My Day."

---

## Screenshot list

Save to `/directives/training/assets/screenshots/library/`:

1. `/library/` category grid, full page
2. Skills category list view
3. Single asset detail page, top half (title, description, how-to-use)
4. Single asset detail page, bottom half (Install panel, ratings, comments)
5. Search results page with query visible
6. Filter sidebar expanded
7. Install button in clicked state (success message visible)
8. The opened GitHub PR — title + Files changed tab
9. An MCP-server detail page where the Install panel is hidden (for contrast)
10. `/library/ingest` URL submission form

---

## Music & captions

Same conventions as Video 01. See [01-intro-video-script.md](01-intro-video-script.md) for details.
