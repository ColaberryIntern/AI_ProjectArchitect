# Library — Written Walkthrough

**Companion to:** [Video 02 — Library Walkthrough](02-library-video-script.md)
**Read this if:** you prefer text to video, you want to skim before watching, or you need to look something up after watching.

---

## What the Library is

The Library at `advisor.colaberry.ai/library/` is Colaberry's shared catalog of reusable building blocks. Six categories:

- **Skills** — focused capabilities Claude can invoke (e.g., `verify`, `code-review`)
- **Agents** — purpose-built subagents with specific roles (e.g., code reviewers, security auditors)
- **Prompts** — vetted prompt templates for common jobs
- **MCP servers** — external tools and data sources Claude can use
- **Use-cases** — full worked examples showing how to solve a specific problem
- **Capabilities** — broader feature descriptions, often spanning multiple of the above

Every asset has been contributed by someone on the team, reviewed, and is ready to drop into your work.

---

## How to find what you need

### Browse by category
Click any category tile on the Library home page. You'll see a list of assets with:
- **Name** and a one-line description
- **Rating** from other users
- **Last updated** timestamp

### Search by keyword
The search box at the top of the Library searches across all categories. Try terms like `playwright`, `review`, `migration`, or the name of a tool you're already using.

### Filter
Once you have results, you can narrow by:
- **Company** — show only assets your company has access to
- **Vetting status** — show only formally reviewed assets vs. all assets
- **Category** — when searching across categories, narrow to one

---

## The asset detail page

Click any asset to open its detail page. Here's what you'll find, top to bottom:

1. **Title and short description** — what it does, in one sentence
2. **How to use** — when to invoke it, what arguments it takes, what it returns
3. **Example** — a real worked example
4. **Ratings** — stars from other users
5. **Comments** — questions, war stories, gotchas
6. **Install panel** *(green box, near the bottom)* — only present for assets you can install

Read at least the "How to use" and one comment before installing — they'll save you debugging time.

---

## Installing an asset

When you find something you want to use, scroll to the green **📥 Install to your workspace** panel.

### What "Install" does

Clicking Install:

1. Bundles the asset's files and dependencies — if a skill depends on a specific agent, the agent comes along.
2. Writes the files to the correct paths in your workspace repository.
3. Opens a pull request in that repo with the changes.
4. Logs the install for audit (in `output/library/_install_audit/`).

You then review and merge the PR like any other change. The asset is live in your workspace once the PR is merged.

### Subscribing to updates

The Install panel has a **"Subscribe to updates"** checkbox. Check it before clicking Install.

When the asset gets a new approved version, the system automatically opens a follow-up PR in your repo with the upgrade. You stay current without having to re-install manually.

You can subscribe even if you've already installed — go back to the asset page, check the box, and click "Subscribe."

### When you can't install

Some assets don't have an Install button. Two reasons:

1. **Live-in-MCP assets.** These are already available to you through your Claude MCP. Installing them locally would risk leaking tokens. Just use them from Claude directly.
2. **You don't have a workspace repo configured.** The Install button is disabled until your account is linked to a workspace_repo. Talk to your admin if you don't have one.

Anonymous (logged-out) viewers don't see the Install panel at all. Log in first.

---

## Rating and commenting

After you've used an asset, go back and leave a rating + a short comment. The comment is more valuable than the rating — a star count tells the next user nothing, but a comment like "Used this on a Stripe webhook fix; the suggested test pattern caught a race condition" tells them everything.

This is how the Library stays useful. Treat it like a code review channel.

---

## Contributing your own assets

If you've built something worth sharing — a skill, a prompt template, a use-case — submit it through `/library/ingest`:

1. Paste a GitHub URL or a docs link.
2. The system scans it, fills in metadata, and shows you what it found.
3. Pick the category, edit anything wrong, and submit.
4. A reviewer approves it (or sends it back with comments).
5. Once approved, it's in the Library and other users can install it.

This is the formal contribution path. For one-off improvements to an existing asset, use the comments section on its detail page instead.

---

## Common questions

**Q: What's the difference between a skill and an agent?**
A skill is a focused capability you can call (`/verify`, `/code-review`). An agent is a more autonomous role with its own context and tool budget — you spawn one and it does multiple steps.

**Q: Why did Install open a PR instead of writing to my repo directly?**
So you can review the change. The system never silently modifies your repo. If the PR looks wrong, close it; nothing was changed in your main branch.

**Q: I installed something and don't see it in Claude. Why?**
The PR has to be merged first. Until then, the asset is in a branch on GitHub, not in your local checkout. After merge, pull the branch (`git pull`) and the asset is available.

**Q: An asset I subscribed to keeps opening upgrade PRs. Can I unsubscribe?**
Yes. Go back to the asset's detail page; the subscribe checkbox will show as checked. Uncheck it and save. You'll stop getting upgrade PRs.

**Q: Can I install an asset to a repo other than my default workspace?**
Not yet through the UI. The current Install button uses your configured workspace_repo. Multi-repo install is on the roadmap.

---

**See also:**
- [Video 02 — Library Walkthrough](02-library-video-script.md) (5 min)
- [Video 03 — My Day Workflow](03-my-day-video-script.md) (5 min) — where Library assets get suggested in context
