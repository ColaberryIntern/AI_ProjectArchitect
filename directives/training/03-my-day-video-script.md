# Video 03 — My Day Workflow: Manage Your Tasks & Emails

**Runtime target:** 4:45–5:30
**Audience:** Everyone who works through Basecamp tasks day-to-day
**Prerequisites:** Viewer has connected Basecamp (see Video 04) and has at least a few active todos assigned to them

**This is the most important video in the package.** This is the daily workflow.

---

## Pre-production notes

- Record on a demo Basecamp project with realistic-looking but non-sensitive task names. Do **not** record real customer tasks.
- Have at least 6–8 todos visible in My Day at record time: a mix of "assigned to me," "due soon," and "unassigned." This shows the urgency tiers clearly.
- Have one task linked to a Gmail thread so Scene 5 (the email flow) has something real to show.

---

## Scene 1 — Open My Day (0:00 – 0:25)

**SHOW:**
Navigate to `https://advisor.colaberry.ai/my-day/`. Pause on the loaded page for 3 seconds so the viewer can take in the layout.

Point cursor sequentially at:
1. The task queue (center of page)
2. The filter sidebar (left or top)
3. The "Sync" button

**SAY:**
> "This is My Day. It's the page you open in the morning, and it's the page that should answer the question: what do I work on right now? In the center, your task queue, ranked by urgency. On the side, filters to narrow what you're looking at. And the Sync button at the top pulls the latest from Basecamp on demand. The page also auto-refreshes in the background, so you don't have to."

---

## Scene 2 — How the queue is ranked (0:25 – 1:15)

**SHOW:**
Hover over the first task in the queue without clicking. Then scroll down so the viewer can see how tasks are grouped or color-coded by urgency tier.

If there are visual tier labels (e.g., "Assigned," "Due soon," "Unassigned"), point at each one in turn.

**SAY:**
> "The queue isn't a flat list. It's tiered. At the top, things assigned directly to you. Then things coming due soon — even if they're not assigned to you, you should know they're on fire. Then unassigned items in projects you own, in case nobody else picks them up. Within each tier, the system uses an urgency score that factors in due date, comment activity, and how long it's been sitting. You work top-down. If something's at the top of the queue, the system thinks it's your most important task right now."

---

## Scene 3 — Filtering and tiers (1:15 – 1:50)

**SHOW:**
Click a tier filter — e.g., show only "Assigned to me." Pause to show the narrower list.

Then click a category filter — e.g., "human_required" vs "waiting_dependency." Show the difference.

Click back to default ("all").

**SAY:**
> "If your queue gets too dense, narrow it. You can filter to just tasks assigned to you — that hides the early-warning stuff. You can also filter by category: 'human required' for tasks that genuinely need you to decide something, 'waiting on a dependency' for tasks blocked on someone else, 'unscored' for new arrivals. Most days, the default view is what you want. The filters are there for when you're overwhelmed and need to focus."

---

## Scene 4 — Clicking into a task (1:50 – 2:40)

**SHOW:**
Click one task to open its detail page (`/my-day/todo/{id}`). Pause for 3 seconds.

Point cursor at:
1. The task title and description (pulled from Basecamp)
2. The due date
3. The "Open in Basecamp" button
4. The "Suggested assets" section (if visible)
5. The "Dismiss" button

**SAY:**
> "Click any task and you get the full picture. The title and description come straight from Basecamp, so you're not switching tabs to read what the task is about. There's the due date. There's a one-click jump to the original Basecamp todo if you need the full thread or want to comment. At the bottom — this is the magic — the system suggests Library assets that look relevant. If the task says 'add a test for X,' it might suggest the `verify` skill, or a Playwright agent. You don't have to remember what's in the Library; the system points at it."

---

## Scene 5 — Where emails fit in (2:40 – 3:40)

**SHOW:**
Cut to a static slide or simple diagram:
```
Gmail inbox  →  attachment_fetch tool  →  Google Drive (staged)
                                    ↘
Customer email  →  Basecamp todo  →  My Day queue
```

Hold the diagram for 5 seconds while narrating, then cut back to a My Day task that has email-derived context (e.g., a task created from a customer request).

**SAY:**
> "Your emails get pulled into this flow too. When a customer email comes in and creates work, a Basecamp todo is opened, and it shows up in your My Day queue just like any other task. If the email had attachments, they're staged into Google Drive under a folder like 'Colaberry Inbound / Gmail / sender name / month' — the system handles the filing. By the time you see the task in My Day, the context is already there. You don't have to go hunt through your inbox to find what the task is about."

---

## Scene 6 — Dismissing what isn't yours (3:40 – 4:15)

**SHOW:**
Back on the queue. Click "Dismiss" on a task that's clearly not for the demo user. Show that it disappears from the queue.

Briefly show — via title card or hover tooltip — that dismiss is local-only:
> *Dismiss is local to My Day. It does not close the Basecamp todo.*

**SAY:**
> "Not every task in your queue is actually yours to do. Maybe it got assigned by mistake, or it's a watch-only ping. Hit Dismiss and it disappears from your queue. Important: this is a *local* dismiss. It does not close the Basecamp todo. The task still exists, the assignee still sees it; you've just told the system 'don't show this to me again unless something changes.' If it gets updated or commented on later, it can come back. This way you can clear your queue without affecting your teammates."

---

## Scene 7 — Sync, refresh, and trust (4:15 – 4:55)

**SHOW:**
Click the **Sync** button. Show the refresh indicator. After it completes, point at the timestamp showing when the last sync ran.

Then briefly show the auto-refresh in action — wait or simulate (you can pre-record this in a longer take and trim).

**SAY:**
> "If you want the latest right now, hit Sync. Otherwise, the page refreshes itself every thirty seconds or so in the background. The timestamp shows you when the data was last pulled. If you ever see something stale, hit Sync. The point of this page is that you can trust it — what you see is what's in Basecamp, plus whatever filters you've set."

---

## Scene 8 — Close (4:55 – 5:15)

**SHOW:**
Title card:
> **Last video: How the Library extends your daily flow**
> *Video 02 in this series (if you haven't watched it already).*

**SAY:**
> "That's the full daily flow. Open My Day, work the top of the queue, dismiss what isn't yours, click into the ones that need context. When a task suggests a Library asset, click through and install it. Over time, this loop tightens — your queue gets cleaner, your Library use gets sharper, and the system gets better at predicting what you need."

---

## Screenshot list

Save to `/directives/training/assets/screenshots/my-day/`:

1. `/my-day/` full page with 6+ tasks visible across tiers
2. Filter sidebar expanded
3. Single task detail page, top half (title, description, due date)
4. Single task detail page, bottom half (suggested assets, Dismiss, Open in Basecamp)
5. The email-to-task flow diagram (Scene 5) — needs to be drawn; see below
6. A dismissed task — show empty state or the queue with one task removed
7. Sync button in mid-refresh state
8. Last-sync timestamp visible

**Diagram for Scene 5** — produce a simple SVG or use draw.io / Excalidraw. Save the source file so it can be edited later:
```
[ Gmail inbox ] ──┐
                  ├──► [ attachment_fetch ] ──► [ Google Drive staging ]
[ Basecamp ]   ───┘
       │
       └──► [ Basecamp todo ] ──► [ My Day queue ]
```

---

## Music & captions

Same conventions as Video 01.
