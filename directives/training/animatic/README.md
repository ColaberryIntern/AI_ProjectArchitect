# Animatic Pipeline

This folder builds **draft "animatic" videos** for the training package. An animatic = title-card slides + TTS narration in an MP4. Useful for:

- **Pacing checks** — see how the script flows before anyone records live footage
- **Producer reference** — every scene has the on-screen plan + the verbatim narration audible
- **Internal preview** — shippable as a v0 training video while final recordings are being produced

**This is not the final training video.** The final videos will have real screen captures of `advisor.colaberry.ai` and (ideally) human narration. See `../07-recording-checklist.md` for the production path.

---

## What's in here

```
animatic/
├── README.md                 # this file
├── scenes.json               # all four videos' scene data (timestamp, heading, ON-SCREEN, narration)
├── build_animatic.py         # renderer: TTS + slide rendering + ffmpeg assembly
├── .gitignore                # excludes work/ intermediate artifacts
└── work/                     # generated; safe to delete and rebuild
    ├── slides/               # one PNG per scene
    ├── audio/                # one WAV + one .txt per scene
    └── clips/                # one MP4 per scene (concatenated into final outputs)
```

Final video outputs land in `../assets/videos/*-animatic.mp4`.

---

## Rebuilding

From this directory:

```powershell
python build_animatic.py
```

Takes ~3-5 minutes for all four videos. Re-running is safe; it overwrites prior outputs.

If you edit `scenes.json` (change narration, add a scene, fix a timestamp), just re-run the script — only the changed video needs to be looked at since it's not incremental, but cost is small.

---

## Editing narration

`scenes.json` is the source of truth for narration. Each scene has:

| Field | Purpose |
|---|---|
| `id` | Internal slug (used in filenames) |
| `timestamp` | What range of the timeline this scene occupies (e.g., "0:15-0:45") |
| `heading` | The large heading shown on the slide |
| `show` | The "ON SCREEN" panel — describes what live footage will look like |
| `say` | The verbatim narration read by TTS (and read by the human narrator for the final cut) |

**Keep `say` strings free of:**
- Em-dashes (`—`) — they sound awkward in TTS; use commas or sentence breaks
- Markdown formatting — TTS reads it literally
- URLs — TTS will fumble them; spell them out ("slash my-day")

The build script already strips em-dashes and ellipses defensively, but writing clean text upstream produces better audio.

---

## Dependencies

- **Python 3.11+** with **Pillow** (already installed in this env)
- **ffmpeg 8.x** — installed via `winget install Gyan.FFmpeg`; path hardcoded at top of `build_animatic.py`
- **Windows System.Speech** — built into Windows; uses the `Zira` voice (female)

If you move this pipeline to a different machine:
1. Update `FFMPEG` / `FFPROBE` paths at the top of `build_animatic.py`.
2. Confirm a TTS voice is available — `Get-Content` the `PS_TTS_TEMPLATE` for the voice selector logic.

---

## Why TTS instead of a real voice

The animatic is a draft, and TTS is free, offline, and instantly regeneratable. The point of these drafts is to validate pacing and copy. Once the script is locked, a human (or a paid voice actor) re-records the SAY column and the final video uses that audio instead.

To swap in a real voice track:
1. Record each scene's narration as a WAV using Audacity (see `../07-recording-checklist.md`).
2. Drop the WAV files into `work/audio/{video-id}-{scene-id}.wav` matching the existing filenames.
3. Re-run `build_animatic.py` — but **comment out the `tts()` call** so it doesn't overwrite your real recordings.

(A flag for "use existing audio if present" could be added — left as a follow-up since you'll likely want a different build path for finals anyway.)

---

## Known limits

- **No live app footage.** Slides describe the on-screen action in text instead. The point is timing/pacing, not visual fidelity.
- **TTS pacing is faster than human.** Expect final cuts to run 10-20% longer than the animatic when re-recorded with real narration and UI interaction pauses.
- **No music bed.** Drafts don't need it; finals should add a soft instrumental bed per `../07-recording-checklist.md`.
- **Single visual style.** All scenes use the same slate-navy slide template. Fine for drafts.

---

**Last built:** 2026-06-09
**Total animatic runtime across all four videos:** ~13:30
