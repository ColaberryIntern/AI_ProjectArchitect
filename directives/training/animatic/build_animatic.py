"""Build animatic MP4s from scenes.json.

Each scene becomes a still-image slide with TTS-narrated audio.
Per-video clips are concatenated into a single MP4 per video,
and (optionally) all four MP4s are concatenated into one combined video.

Outputs:
  ../assets/videos/{video_id}-animatic.mp4
  ../assets/videos/all-animatic.mp4  (combined)

Intermediate files land in ./work/ and can be safely deleted.

Run from this directory:
  python build_animatic.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import visuals

# Narration voice — edge-tts neural voice. Override here to switch.
# Top picks: en-US-AvaNeural (warm female), en-US-AndrewNeural (warm male),
# en-US-EmmaNeural (younger), en-US-BrianNeural (deeper male).
EDGE_VOICE = "en-US-AvaNeural"
EDGE_RATE = "-5%"   # slightly slower than default for clarity

# --- paths ------------------------------------------------------------------

HERE = Path(__file__).parent.resolve()
SCENES_JSON = HERE / "scenes.json"
WORK = HERE / "work"
SLIDES = WORK / "slides"
AUDIO = WORK / "audio"
CLIPS = WORK / "clips"
OUT = (HERE.parent / "assets" / "videos").resolve()

FFMPEG = r"C:\Users\ali_m\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"
FFPROBE = r"C:\Users\ali_m\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffprobe.exe"

# --- visual style -----------------------------------------------------------

W, H = 1920, 1080
BG = "#0f172a"            # slate-900
ACCENT = "#22d3ee"        # cyan-400
TEXT_PRIMARY = "#f8fafc"  # slate-50
TEXT_MUTED = "#94a3b8"    # slate-400
PANEL = "#1e293b"         # slate-800
PANEL_BORDER = "#334155"  # slate-700

PADDING = 90


def find_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def fonts(size: int) -> ImageFont.FreeTypeFont:
    return find_font(
        ["seguisb.ttf", "segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"], size
    )


def fonts_bold(size: int) -> ImageFont.FreeTypeFont:
    return find_font(
        ["seguibl.ttf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"], size
    )


# --- slide rendering --------------------------------------------------------

def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = cur + " " + w
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def draw_text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    font,
    color: str,
    max_width: int,
    line_spacing: int = 12,
) -> int:
    """Render wrapped text. Returns total height consumed."""
    x, y = xy
    lines = wrap_text(draw, text, font, max_width)
    total = 0
    for line in lines:
        draw.text((x, y + total), line, font=font, fill=color)
        bbox = draw.textbbox((0, 0), line, font=font)
        total += (bbox[3] - bbox[1]) + line_spacing
    return total


def render_slide(
    video_title: str,
    video_subtitle: str,
    scene: dict,
    scene_idx: int,
    total_scenes: int,
    out_path: Path,
) -> None:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Top bar
    draw.text(
        (PADDING, 40),
        video_subtitle.upper(),
        font=fonts_bold(24),
        fill=ACCENT,
    )
    progress_text = f"SCENE {scene_idx} / {total_scenes}     {scene['timestamp']}"
    bbox = draw.textbbox((0, 0), progress_text, font=fonts(22))
    draw.text(
        (W - PADDING - (bbox[2] - bbox[0]), 42),
        progress_text,
        font=fonts(22),
        fill=TEXT_MUTED,
    )

    # Scene heading
    draw.text(
        (PADDING, 80),
        scene["heading"],
        font=fonts_bold(56),
        fill=TEXT_PRIMARY,
    )

    # Tiny cyan rule under the heading
    draw.rectangle([(PADDING, 160), (PADDING + 80, 164)], fill=ACCENT)

    # Mockup area — the centerpiece
    mockup_y0 = 200
    mockup_y1 = H - 200
    mockup_box = (PADDING, mockup_y0, W - PADDING, mockup_y1)
    mockup_name = scene.get("mockup", "title_card")
    mockup_ctx = scene.get("mockup_ctx", {}) or {}
    # supply title-card defaults from scene heading if missing
    if mockup_name == "title_card":
        mockup_ctx = {**{"title": scene["heading"], "subtitle": scene["show"]}, **mockup_ctx}
    visuals.render_mockup(mockup_name, draw, mockup_box, mockup_ctx)

    # Narration strip at the bottom: short "now saying..." quote from SAY
    say = scene.get("say", "")
    # take the first sentence-ish chunk (up to ~140 chars at a sentence boundary)
    snippet = say
    if len(snippet) > 140:
        # find last sentence end before 140
        cut = 140
        for end in (".", "!", "?"):
            i = say.rfind(end, 0, 140)
            if i > 40:
                cut = i + 1
                break
        snippet = say[:cut].strip() + " …"
    strip_y = H - 160
    draw.rounded_rectangle(
        [(PADDING, strip_y), (W - PADDING, strip_y + 90)],
        radius=14,
        fill=PANEL,
        outline=PANEL_BORDER,
        width=1,
    )
    draw.text(
        (PADDING + 24, strip_y + 12),
        "NARRATION",
        font=fonts_bold(16),
        fill=ACCENT,
    )
    draw_text_block(
        draw,
        snippet,
        (PADDING + 24, strip_y + 38),
        fonts(22),
        TEXT_PRIMARY,
        W - 2 * PADDING - 48,
        line_spacing=4,
    )

    # Footer
    draw.text(
        (PADDING, H - 50),
        "AI Project Architect & Build Companion  |  advisor.colaberry.ai",
        font=fonts(18),
        fill=TEXT_MUTED,
    )
    draw.text(
        (W - PADDING - 280, H - 50),
        "[Animatic Draft - TTS Narration]",
        font=fonts(18),
        fill=TEXT_MUTED,
    )

    img.save(out_path, "PNG")


# --- TTS via Windows System.Speech -----------------------------------------

def _clean_for_tts(text: str) -> str:
    return text.replace("—", " — ").replace("…", "...")


async def _edge_tts_to_mp3(text: str, voice: str, rate: str, out_mp3: Path) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(str(out_mp3))


def tts(text: str, out_wav: Path, tmp_txt: Path) -> None:
    """Generate narration audio using edge-tts neural voices.

    edge-tts produces MP3; we convert to WAV via ffmpeg for consistent
    pipeline downstream (audio_duration / concat both behave best with WAV).
    """
    clean = _clean_for_tts(text)
    tmp_txt.write_text(clean, encoding="utf-8")

    # If WAV already exists and is non-empty, skip — lets us iterate the build
    # without regenerating audio for unchanged scenes. Delete work/audio/*.wav
    # to force a rebuild.
    if out_wav.exists() and out_wav.stat().st_size > 1024:
        return

    tmp_mp3 = out_wav.with_suffix(".mp3")
    asyncio.run(_edge_tts_to_mp3(clean, EDGE_VOICE, EDGE_RATE, tmp_mp3))

    # Convert MP3 -> WAV (PCM 48k stereo) so ffprobe duration + concat are clean.
    cmd = [FFMPEG, "-y", "-i", str(tmp_mp3), "-ar", "48000", "-ac", "2",
           "-c:a", "pcm_s16le", str(out_wav)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"MP3→WAV failed for {out_wav.name}: {r.stderr[-1000:]}")
    try:
        tmp_mp3.unlink()
    except OSError:
        pass


# --- ffmpeg helpers ---------------------------------------------------------

def audio_duration(path: Path) -> float:
    r = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(r.stdout.strip())


def build_scene_clip(slide: Path, audio: Path, out_mp4: Path, pad_seconds: float = 0.6) -> None:
    """Build a single scene clip: still image + audio, with a short pad of
    silence at the end so cuts don't feel abrupt."""
    # cache: skip if the clip is newer than both inputs
    if out_mp4.exists():
        clip_mtime = out_mp4.stat().st_mtime
        if clip_mtime > slide.stat().st_mtime and clip_mtime > audio.stat().st_mtime:
            return
    cmd = [
        FFMPEG,
        "-y",
        "-loop",
        "1",
        "-i",
        str(slide),
        "-i",
        str(audio),
        "-filter_complex",
        f"[1:a]apad=pad_dur={pad_seconds}[a]",
        "-map",
        "0:v",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-tune",
        "stillimage",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(out_mp4),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg scene clip failed for {out_mp4.name}:\n{r.stderr[-2000:]}")


def concat_clips(clip_paths: list[Path], out_mp4: Path) -> None:
    """Concat using the ffmpeg concat filter, with re-encode.

    Earlier versions used the concat demuxer with -c copy. That's faster but
    relies on the source clips having perfectly consistent timebase / SPS /
    PTS — which still-image-derived clips often don't. Symptoms: audio drifts
    ahead of video, or playback stalls at a clip boundary. Re-encoding via
    the concat filter rewrites all timestamps cleanly and produces a single
    coherent stream. Cost is ~1-2 minutes per 10 min of output on x264
    veryfast / CRF 22 — acceptable.
    """
    n = len(clip_paths)
    inputs: list[str] = []
    for p in clip_paths:
        inputs.extend(["-i", str(p)])
    filter_str = (
        "".join(f"[{i}:v][{i}:a]" for i in range(n))
        + f"concat=n={n}:v=1:a=1[v][a]"
    )
    cmd = [
        FFMPEG, "-y", *inputs,
        "-filter_complex", filter_str,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(out_mp4),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed for {out_mp4.name}:\n{r.stderr[-2000:]}")


# --- main -------------------------------------------------------------------

def main() -> int:
    for d in (WORK, SLIDES, AUDIO, CLIPS, OUT):
        d.mkdir(parents=True, exist_ok=True)

    data = json.loads(SCENES_JSON.read_text(encoding="utf-8"))

    summary: list[tuple[str, float, int]] = []  # (video_id, total_sec, n_scenes)

    for video in data["videos"]:
        vid = video["id"]
        print(f"\n=== Building {vid}: {video['title']} ===")
        scene_clips: list[Path] = []
        total_audio_sec = 0.0

        scenes = video["scenes"]
        for i, scene in enumerate(scenes, start=1):
            scene_key = f"{vid}-{scene['id']}"
            slide_path = SLIDES / f"{scene_key}.png"
            audio_path = AUDIO / f"{scene_key}.wav"
            txt_path = AUDIO / f"{scene_key}.txt"
            clip_path = CLIPS / f"{scene_key}.mp4"

            # 1. Slide
            render_slide(
                video["title"], video["subtitle"], scene, i, len(scenes), slide_path
            )

            # 2. TTS audio
            tts(scene["say"], audio_path, txt_path)
            dur = audio_duration(audio_path)
            total_audio_sec += dur

            # 3. Scene clip
            build_scene_clip(slide_path, audio_path, clip_path)
            scene_clips.append(clip_path)

            print(f"  [{i}/{len(scenes)}] {scene['heading']}  ({dur:.1f}s)")

        # 4. Concatenate
        final_mp4 = OUT / f"{vid}-animatic.mp4"
        concat_clips(scene_clips, final_mp4)
        print(f"  -> {final_mp4.relative_to(HERE.parent.parent)}  (~{total_audio_sec:.0f}s)")
        summary.append((vid, total_audio_sec, len(scenes)))

    # Combine all four videos into one
    print("\n=== Combining all videos ===")
    all_clips = [OUT / f"{vid}-animatic.mp4" for vid, _, _ in summary]
    combined = OUT / "all-animatic.mp4"
    concat_clips(all_clips, combined)
    total = sum(s for _, s, _ in summary)
    m, s = divmod(int(total), 60)
    print(f"  -> {combined.name}  (~{m}:{s:02d})")

    print("\n=== Done ===")
    for vid, sec, n in summary:
        m, s = divmod(int(sec), 60)
        print(f"  {vid}-animatic.mp4  -  {n} scenes  -  ~{m}:{s:02d}")
    print(f"  all-animatic.mp4      -  combined  -  ~{int(total)//60}:{int(total)%60:02d}")
    print(f"\nOutputs: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
