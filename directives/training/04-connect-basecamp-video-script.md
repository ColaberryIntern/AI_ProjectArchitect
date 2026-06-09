# Video 04 — Connect Your Basecamp Account (One-Time Setup)

**Runtime target:** 1:45–2:15
**Audience:** First-time users, before they can use My Day
**Prerequisites:** User has a Basecamp account and access to at least one project

---

## Pre-production notes

- Record this on a fresh demo account so the OAuth screens look like a first-time experience.
- The Basecamp OAuth screen may change without notice — this scene is the most likely to need re-recording over time. Keep the segment short and the language generic so small UI shifts don't break it.
- **Do not record real credentials or tokens.** Use a sandbox Basecamp org.

---

## Scene 1 — Why connect (0:00 – 0:20)

**SHOW:**
Static slide:
> **Before you can use My Day, the system needs to read your Basecamp tasks.**
> *This is a one-time setup. Two minutes.*

Then dissolve to the homepage of `advisor.colaberry.ai` while logged in.

**SAY:**
> "Before you can use My Day, you need to connect your Basecamp account. This is a one-time step. It lets the system read the tasks assigned to you and post updates back to Basecamp on your behalf — with your name on them, not a bot's. Let's do it now."

---

## Scene 2 — Open Connect Basecamp (0:20 – 0:45)

**SHOW:**
Navigate to `https://advisor.colaberry.ai/profile/connect-basecamp`. Pause on the page so the viewer can see what's there.

**SAY:**
> "Go to Profile, Connect Basecamp. Or paste the URL directly: /profile/connect-basecamp. You'll see a page explaining what the system will be able to do once connected, and a button to start the connection."

---

## Scene 3 — Basecamp OAuth (0:45 – 1:25)

**SHOW:**
Click the **Connect** button. The browser redirects to Basecamp's OAuth screen. Pause on it.

Click "Allow access" on Basecamp's screen. Wait for the redirect back to advisor.colaberry.ai.

Show the success state — "Basecamp connected" or similar.

**SAY:**
> "Click Connect. Basecamp opens its own authorization screen. This is Basecamp asking you — not us — whether you want to grant the system access. Read what it's asking for. Click Allow. Basecamp redirects you back to our site, and you should see a success message confirming the connection. That's it."

---

## Scene 4 — Verify it worked (1:25 – 1:50)

**SHOW:**
Navigate to `/my-day/`. Show that the page now loads with actual tasks (not an empty "connect first" state).

**SAY:**
> "To confirm it worked, go to My Day. You should see your tasks loading in. If you see an empty state asking you to connect Basecamp, the connection didn't take — go back and try again. If you see your tasks, you're done. You don't need to do this setup again."

---

## Scene 5 — What "via your Claude Code" means (1:50 – 2:10)

**SHOW:**
Cut to a Basecamp todo (in a browser tab on basecamp.com) with a comment that reads:
> *via Ali's Claude Code*
> [comment body]

Hold for 4 seconds.

**SAY:**
> "One thing to know. Now that you're connected, when the system posts to Basecamp on your behalf — closing a task, adding a comment, attaching a file — the post is tagged with your name. Your teammates see 'via your Claude Code,' so they know it was you, working with AI assistance, not some anonymous integration. This keeps the trust right where it should be: between people."

---

## Scene 6 — Close (2:10 – 2:20)

**SHOW:**
Title card:
> **You're set up. Now go to Video 03 — My Day Workflow.**

**SAY:**
> "You're set up. Next, watch the My Day video to see what to do with the queue."

---

## Screenshot list

Save to `/directives/training/assets/screenshots/connect-basecamp/`:

1. `/profile/connect-basecamp` landing page
2. Basecamp's OAuth authorization screen (sanitize org name)
3. Success state after redirect back
4. `/my-day/` with tasks loaded (post-connection)
5. A Basecamp todo showing "via [Name]'s Claude Code" attribution

---

## Music & captions

Same conventions as Video 01.
