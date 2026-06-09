# My Day — Written Walkthrough

**Companion to:** [Video 03 — My Day Workflow](03-my-day-video-script.md)
**Read this if:** you prefer text, you want a quick reference for the daily flow, or you're onboarding a new team member who can't watch video right now.

---

## What My Day is for

`advisor.colaberry.ai/my-day/` answers one question: **what should I work on right now?**

It pulls every Basecamp task you might care about — assigned to you, coming due, unassigned in projects you own — ranks them by urgency, and surfaces them in a single view. It also surfaces work that arrived via email, because customer emails create Basecamp todos that flow into the same queue.

You should be able to open My Day in the morning, work top-down, and trust that nothing important is hiding from you.

---

## Daily flow, in one paragraph

Open `/my-day/`. Look at the top of the queue. Click into the first task. Read the context (title, description, due date, suggested Library assets). Decide: do this now, defer it, dismiss it, or open Basecamp to discuss. Repeat with the next task. When you need a tool that doesn't exist yet, search the Library. When you build something reusable, contribute it back. That's the loop.

---

## What's in the queue

### Tiers

The queue is grouped into tiers, top to bottom by urgency:

1. **Assigned to you** — direct assignments. Default focus.
2. **Due soon (any assignee)** — early warning for things on fire, even if not yours.
3. **Unassigned in your projects** — items nobody has picked up; you might be the right owner.

Within each tier, items are ranked by an **urgency score** that factors in:
- How close the due date is
- Recent comment activity (active conversations score higher)
- Age (how long it's been sitting untouched)

You don't tune this manually. Trust the order.

### Categories

Each task is also tagged with a category:

- **`human_required`** — needs a person to decide something. These are the real work.
- **`waiting_dependency`** — blocked on someone else or an external system. Skip unless the dependency cleared.
- **`unscored`** — too new for the system to categorize yet. Glance at it; if it's yours, work it.

You can filter by category from the sidebar.

---

## Working a task

Click any row in the queue to open `/my-day/todo/{id}`. You'll see:

| Section | What it is |
|---|---|
| **Title & description** | Pulled from Basecamp; what the task actually is |
| **Due date** | If set in Basecamp |
| **Open in Basecamp** | One-click jump to the full Basecamp thread |
| **Suggested assets** | Library items the system thinks match this task (skills, agents, use-cases) |
| **Dismiss** | Soft-dismiss the task from your queue (does not affect Basecamp) |

The suggested assets section is the highest-leverage part of this page. If the task is "add a Playwright test for the checkout flow," you might see the `verify` skill, a Playwright agent, and a checkout-testing use-case suggested. Click any of them to read the asset, then install if useful.

---

## How emails become tasks

Your email isn't a separate system in this workflow. Here's what happens:

```
Customer email arrives in Gmail
            │
            ▼
Email triggers BC todo creation (in the right project)
            │
            ├─► Attachments staged to Google Drive
            │   under "Colaberry Inbound / Gmail / [sender] / [YYYY-MM]"
            │
            ▼
Todo appears in My Day queue with email context
            │
            ▼
You see it ranked alongside other Basecamp work
```

By the time the task hits your queue, the context is there. The email body is in the todo description; attachments are filed in Drive with a stable path. You don't have to dig through your inbox.

**What this means for your inbox habits:** you can stop treating Gmail as a task list. If a customer email needs work, it'll show up in My Day. If it doesn't show up in My Day, it didn't create work.

---

## Sync, refresh, trust

- The page **auto-refreshes** every ~30 seconds in the background.
- Hit the **Sync** button at the top if you want the latest right now (after closing something in Basecamp, for example).
- The **last-sync timestamp** at the top tells you how stale the view is. If it's more than a minute old and you haven't seen activity, hit Sync.

You should be able to trust this page. If you ever see something stale, that's a bug — report it.

---

## Dismissing

If a task in your queue isn't actually yours to do — assigned by mistake, watch-only ping, or just noise — click **Dismiss**.

**Important: dismiss is local.** It only hides the task from *your* My Day view. It does not:
- Close the Basecamp todo
- Reassign it
- Notify anyone

The task still exists in Basecamp, and other assignees still see it. You've just told *your* My Day "don't bother me about this." If the task gets meaningful new activity later (new comment, due date change, reassignment), the system may bring it back.

Use Dismiss freely. It costs nothing and makes your queue cleaner.

---

## What "via your Claude Code" means

When you work tasks through Claude Code — closing a todo, posting a comment, attaching a file — the post is tagged with your name:

> *via Ali's Claude Code*
> [comment body]

Your teammates see who did the work and that AI assisted. Two reasons this matters:

1. **Accountability** — your name is on it, so your team trusts it.
2. **Visibility** — your team can see AI assistance is being used, which builds shared norms instead of hidden practice.

You don't have to do anything to get this attribution. It's automatic once you've connected Basecamp.

---

## Common questions

**Q: A task showed up in My Day but I think it's a duplicate. What do I do?**
Dismiss it from your view. If it's a true duplicate at the Basecamp level, raise it in the Basecamp thread — that's where the resolution has to happen.

**Q: I dismissed a task and now I want it back. How?**
Hit Sync. If it had real activity since you dismissed it, it should reappear. If not, search for it in Basecamp directly.

**Q: My queue is empty. Is that right?**
Usually yes — it means you have nothing assigned, nothing coming due imminently, and no unassigned items in your projects. Use the time. Or browse the Library and build something useful.

**Q: The queue is dozens of items long. Help.**
Filter by **`assigned`** tier and **`human_required`** category. That collapses to just the things that genuinely need you. Work that subset top-down. Come back to the full view when you have breathing room.

**Q: A task suggests a Library asset but I don't think it's relevant. Should I report it?**
Yes — leave a comment on the asset page saying "this got suggested for task X but didn't fit because Y." The matching gets better when people report mismatches.

**Q: Can I add a task manually?**
Not through My Day. Create it in Basecamp; it'll sync into your queue within ~30 seconds.

---

**See also:**
- [Video 03 — My Day Workflow](03-my-day-video-script.md) (5 min)
- [Video 04 — Connect Your Basecamp Account](04-connect-basecamp-video-script.md) (2 min) — prerequisite
- [Library companion doc](05-companion-doc-library.md) — when suggested assets need exploring
