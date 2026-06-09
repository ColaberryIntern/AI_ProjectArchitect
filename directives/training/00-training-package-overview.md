# Training Package — AI Project Architect & Build Companion

**Audience:** New users of the `advisor.colaberry.ai` platform — operators, project leads, and team members who will manage their day-to-day work (tasks, emails, project assets) through this system.

**Goal:** After completing this training, a user can confidently (1) understand what the product is for, (2) connect their Basecamp account, (3) triage their day from `/my-day/`, (4) find and install reusable assets from `/library/`, and (5) understand how their emails and tasks flow through the system.

---

## What's in this package

| # | File | Format | Length | Audience |
|---|------|--------|--------|----------|
| 01 | [Intro video script](01-intro-video-script.md) | Video | ~3 min | Everyone (watch first) |
| 02 | [Library walkthrough script](02-library-video-script.md) | Video | ~5 min | Everyone |
| 03 | [My Day workflow script](03-my-day-video-script.md) | Video | ~5 min | Everyone (the core daily workflow) |
| 04 | [Connect Basecamp script](04-connect-basecamp-video-script.md) | Video | ~2 min | First-time users |
| 05 | [Library companion doc](05-companion-doc-library.md) | Written | — | Reference / text learners |
| 06 | [My Day companion doc](06-companion-doc-my-day.md) | Written | — | Reference / text learners |
| 07 | [Recording checklist](07-recording-checklist.md) | Producer doc | — | Whoever records the videos |

**Total runtime if all four videos are watched back-to-back: ~15 minutes.**

---

## Recommended viewing order

```
01 Intro  →  04 Connect Basecamp  →  03 My Day  →  02 Library
   (what)        (one-time setup)      (daily use)     (power use)
```

This order matches how a new user actually adopts the product: understand it, set it up once, then use it daily, then go deeper.

---

## How these scripts are written

Each video script has three columns of information per scene:

- **TIME** — running timestamp (`[00:00–00:15]`)
- **SHOW** — exactly what to capture on screen (URL, click target, scroll position) — captured live in OBS Studio
- **SAY** — verbatim voiceover, written for a calm, neutral narrator

The producer reads SAY into the mic while OBS records SHOW. No improvisation required. The companion docs (05, 06) cover the same material in writing for users who prefer to read.

---

## Production notes

- See [07-recording-checklist.md](07-recording-checklist.md) for tooling, audio settings, screenshot conventions, and the "where do final MP4s live" answer.
- Recommended tools (all OSS): **OBS Studio** for capture, **Shotcut** for editing, **Audacity** for narration cleanup. None require accounts or licenses.
- Total estimated production time for all four videos: **half a day** for someone who has done video editing before; a full day if it's their first time.

---

## Maintenance

These scripts reference specific routes, UI elements, and copy. When the product UI changes meaningfully:

1. Update the affected script's `SHOW` column.
2. Re-record only the affected scenes (each scene is short and self-contained — that's intentional).
3. Update the corresponding companion doc.
4. Bump the version in the file header.

**Do not let videos rot.** A wrong tutorial is worse than no tutorial.

---

**Owner:** Ali Muwwakkil
**Last reviewed:** 2026-06-09
