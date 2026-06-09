# Recording Checklist — For the Producer

**Audience:** Whoever records the four video scripts (could be Ali, could be a hired editor, could be a future Claude session).
**Goal:** Hand this to a producer with the four scripts; they have everything they need to produce final MP4s without coming back with questions.

---

## Tooling (all free, all OSS or no-cost)

| Job | Tool | Notes |
|---|---|---|
| Screen capture | **OBS Studio** | https://obsproject.com — Windows, free, no account |
| Voiceover record | **Audacity** | https://audacityteam.org — free, cleans up noise well |
| Editing / cuts / overlays | **Shotcut** | https://shotcut.org — easiest OSS editor for tutorials |
| (Optional polish) | **DaVinci Resolve Free** | Overkill, but free, broadcast quality |
| Captions | **whisper.cpp** or OBS's caption export | Generates .srt from final audio |
| Diagram for Scene 5 of Video 03 | **Excalidraw** (excalidraw.com) or draw.io | Save the source file for future edits |

**Do not use:** Camtasia, Snagit Premium, ScreenFlow, or any tool that requires a license or watermarks output. The whole point is that this training package is maintainable by anyone.

---

## OBS settings

Use these settings the first time and save them as a profile so the next recording is one click.

**Video:**
- Base canvas: **1920 × 1080**
- Output (scaled): **1920 × 1080**
- FPS: **30** (don't go to 60 — file sizes balloon, no benefit for a screen tutorial)
- Encoder: **x264** (or NVENC if you have an Nvidia GPU)
- Rate control: **CBR**, bitrate **6000 Kbps**

**Audio:**
- Sample rate: **48 kHz**
- Channels: **Stereo**
- Track 1: **Mic only** (record voiceover separately if possible — better quality)
- Mute desktop audio during recording — system notifications will ruin a take.

**Scenes:**
Create one OBS scene per video. Each scene has a single source: "Display Capture" on your primary monitor. Don't capture webcam.

---

## Recording environment

1. **Close everything you don't need.** Notifications, Slack, Outlook, Teams — all closed. A toast popup mid-recording forces a re-take.
2. **Use a clean browser profile.** Bookmarks bar hidden, no extensions visible. Profile picture should be the demo account, not your real one if recording on a demo Basecamp / Gmail.
3. **Set browser zoom to 110–125%.** Default zoom is too small for video viewers on phones. Test on a phone before committing.
4. **Hide the cursor when not actively pointing.** OBS has a "Hide cursor" toggle per source.
5. **Mouse movement: slow and deliberate.** Move to the element, pause for half a beat, then click. Fast mouse movement is hard to follow on video.

---

## Audio setup

A bad mic ruins good content. A decent mic on a quiet recording is more than enough — it doesn't have to be a studio mic.

**Minimum acceptable:**
- USB condenser mic (Blue Snowball, Samson Q2U, FIFINE T669) — $50–80 range
- Pop filter (a foam ball over the mic is fine)
- Recording room: carpet on the floor, soft furniture, no echo

**Audacity post-processing pipeline** (apply in this order):
1. **Noise Reduction** — sample 1 sec of room tone, apply at default settings
2. **Normalize** to -3 dB
3. **Compressor** — default settings; smooths volume variation
4. **High-pass filter** at 80 Hz — kills room rumble
5. Export as WAV (not MP3) for the editor; convert to MP3 only for the final captioned-audio track if needed

---

## Recording the scripts

For each video script in the package:

1. **Read the script all the way through twice** before pressing record. Note any words you stumble on; mark alternatives.
2. **Record voiceover first**, screen second. Reading while clicking is exhausting and produces flat narration.
3. **Capture each scene as its own clip.** Don't try to nail a 5-min video in one take — record scene by scene, edit them together. The scripts are written to support this (each scene is self-contained).
4. **Leave 1 second of silence at the start and end** of every clip. Makes editing easier.
5. **If you flub a line, leave a 2-second silence, then re-do the line.** Don't restart the whole take.

---

## Screenshot capture conventions

The video scripts list specific screenshots needed for the editor. Capture them with these rules:

- **Resolution:** 1920 × 1080 minimum, captured at the same browser zoom used in the video.
- **Format:** PNG (lossless) for screenshots; JPG only if file size is a problem.
- **Naming:** `video01-scene03-library-detail.png` — video number, scene number, what it shows.
- **Storage:** `/directives/training/assets/screenshots/{videoNN}/`
- **Sanitization:** Blur or replace real names, real email addresses, real customer task titles. Use the demo account.

If a screenshot includes a logged-in state, **double-check that the user-name visible is the demo user**, not yours.

---

## Privacy / compliance checklist

Before you publish ANY recording, verify:

- [ ] No real customer names visible in Basecamp todos
- [ ] No real customer emails visible in Gmail clips
- [ ] No real attachments visible in Drive previews
- [ ] No internal Basecamp project names that reveal client identity
- [ ] No live API tokens, OAuth codes, or session cookies visible in browser dev tools, URL bars, or page content
- [ ] No internal hostnames that aren't `advisor.colaberry.ai`
- [ ] Cursor doesn't hover over sensitive bookmarks during screen capture

If you're unsure, **err on the side of blurring it.** Easier to blur and ship than to ship and recall.

---

## Output

For each video, produce:

| File | Purpose |
|---|---|
| `videoNN-final.mp4` | Final cut, 1080p, H.264, AAC audio |
| `videoNN-final.srt` | Caption file generated from voiceover |
| `videoNN-thumbnail.png` | 1280×720 thumbnail for the video index page |

Drop all final assets into:
```
/directives/training/assets/videos/
```

(This folder will need to be in `.gitignore` if videos are large — discuss with Ali before committing MP4s to the repo. Likely they should live in cloud storage, with the repo storing only a manifest pointing to URLs.)

---

## Final pass before shipping

Before you say "done":

1. **Watch each video end-to-end on a phone.** Tutorials are watched on phones surprisingly often. If text on screen is too small to read on a phone, re-zoom and re-record those scenes.
2. **Test the captions.** Turn on captions, mute the audio, watch the whole thing. Captions should make sense without audio.
3. **Time-check.** Each video should be within ±20% of its target runtime. If it's significantly longer, cut filler; if shorter, you probably skipped a scene.
4. **Have one person who hasn't seen the product watch the intro video.** Ask them: "What does this product do?" If they can't answer in one sentence, the intro needs work.
5. **Have one person who *uses* the product watch the My Day video.** Ask them: "Did this miss anything you do every day?" If yes, add it.

---

## Re-recording cadence

The product is changing. Expect to re-record:

- **Scene-level re-records:** when a specific UI element changes (button moves, panel renames). Cheap — single scene, single take.
- **Whole-video re-records:** when a workflow fundamentally changes (e.g., Install panel adds a new mandatory field). Maybe twice a year.
- **Full package re-record:** if the product is rearchitected. Hopefully never — but if it happens, the scripts themselves are the durable artifact, and re-recording from updated scripts is straightforward.

**Update the corresponding script first, then re-record.** Never re-record from memory.

---

**Owner:** Ali Muwwakkil
**Producer (TBD):** _________
**Last reviewed:** 2026-06-09
