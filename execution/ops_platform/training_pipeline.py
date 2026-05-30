"""Training video pipeline — orchestration layer for richer training assets.

What this is NOT
----------------
This does NOT spin up ffmpeg, headless Chrome, or a TTS service. Doing so
would commit us to ~$3-5/min training-asset generation cost on every refresh
plus a non-trivial infra footprint. The user explicitly said: orchestration
layer first, expensive renderers later.

What this IS
------------
A deterministic asset-and-timeline generator that, given a capability id,
produces:

  - voice_script.md        — narrated walkthrough text (sentence-per-line)
  - slides.json            — slide deck spec (title + bullets per slide)
  - storyboard.json        — timeline mapping slides to script sentences with
                             estimated durations
  - screenshots/           — directory where the screenshot hook drops PNGs
                             (URL+selector recorded, no capture executed here)
  - manifest.json          — top-level asset bundle with metadata

The actual video rendering pipeline (audio synthesis, slide → image,
ffmpeg merge) reads this directory and produces the final file. We can
swap that backend later without touching this orchestration.

Outputs land at: ``output/ops_platform/training_assets/{capability_id}/``
"""

from __future__ import annotations

import json
import logging
import re
import textwrap
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import training_agent
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

_ASSETS_DIR = OUTPUT_DIR / "ops_platform" / "training_assets"

# Speaking-rate assumption for timeline estimation. Tunable later via config.
_WORDS_PER_MINUTE = 150
_TRANSITION_SECONDS = 1.0


@dataclass
class Slide:
    slide_id: str
    title: str
    bullets: list[str] = field(default_factory=list)
    speaker_notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StoryboardEntry:
    slide_id: str
    script_sentence_indices: list[int]
    estimated_duration_seconds: float
    suggested_screenshot: dict | None = None  # {url, selector, description}

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrainingAssetBundle:
    capability_id: str
    generated_at: str
    voice_script_path: str
    slides_path: str
    storyboard_path: str
    screenshots_dir: str
    manifest_path: str
    total_duration_seconds: float
    slide_count: int
    script_word_count: int

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def generate_assets(
    capability_id: str,
    *,
    registry: CapabilityRegistry | None = None,
) -> TrainingAssetBundle:
    """Generate the full asset bundle for a capability. Reuses any existing
    walkthrough markdown as the source of truth for the voice script."""
    reg = registry or default_registry()
    cap = reg.get(capability_id)
    if cap is None:
        raise ValueError(f"capability '{capability_id}' not found")

    # 1. Source the walkthrough. Generate one on the fly if missing.
    walkthrough = training_agent.get_training_markdown(capability_id)
    if walkthrough is None:
        result = training_agent.generate_training(capability_id, registry=reg)
        walkthrough = result.markdown

    # 2. Decompose into slides.
    slides = _markdown_to_slides(walkthrough, capability_name=cap.get("name", capability_id))

    # 3. Voice script = walkthrough text normalized to one sentence per line.
    voice_script = _markdown_to_voice_script(walkthrough)
    sentences = [s for s in voice_script.split("\n") if s.strip()]
    word_count = sum(len(s.split()) for s in sentences)

    # 4. Storyboard: distribute sentences across slides, estimate timing.
    storyboard = _build_storyboard(slides, sentences, capability_id=capability_id)
    total_duration = sum(e.estimated_duration_seconds for e in storyboard)

    # 5. Persist.
    out_dir = _ASSETS_DIR / capability_id
    screenshots_dir = out_dir / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    voice_path = out_dir / "voice_script.md"
    slides_path = out_dir / "slides.json"
    storyboard_path = out_dir / "storyboard.json"
    manifest_path = out_dir / "manifest.json"

    voice_path.write_text(voice_script, encoding="utf-8")
    slides_path.write_text(
        json.dumps([s.to_dict() for s in slides], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    storyboard_path.write_text(
        json.dumps([e.to_dict() for e in storyboard], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    bundle = TrainingAssetBundle(
        capability_id=capability_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        voice_script_path=str(voice_path),
        slides_path=str(slides_path),
        storyboard_path=str(storyboard_path),
        screenshots_dir=str(screenshots_dir),
        manifest_path=str(manifest_path),
        total_duration_seconds=round(total_duration, 1),
        slide_count=len(slides),
        script_word_count=word_count,
    )
    manifest_path.write_text(
        json.dumps(bundle.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return bundle


def get_bundle(capability_id: str) -> dict | None:
    manifest_path = _ASSETS_DIR / capability_id / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def record_screenshot_hook(
    capability_id: str, *, slide_id: str, url: str, selector: str | None = None,
    description: str = "",
) -> Path:
    """Register that a screenshot should exist for a slide. Persists a
    pending-screenshot stub the renderer backend will pick up."""
    out_dir = _ASSETS_DIR / capability_id / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    stub_path = out_dir / f"{slide_id}.pending.json"
    stub_path.write_text(
        json.dumps({
            "capability_id": capability_id,
            "slide_id": slide_id,
            "url": url,
            "selector": selector,
            "description": description,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }, indent=2),
        encoding="utf-8",
    )
    return stub_path


def list_bundles() -> list[dict]:
    if not _ASSETS_DIR.exists():
        return []
    out: list[dict] = []
    for sub in sorted(_ASSETS_DIR.iterdir()):
        if not sub.is_dir():
            continue
        manifest = sub / "manifest.json"
        if manifest.exists():
            try:
                out.append(json.loads(manifest.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
    return out


# ── Internal ───────────────────────────────────────────────────────────


_HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)


def _markdown_to_slides(md: str, *, capability_name: str) -> list[Slide]:
    """Split a walkthrough into slides by H1/H2/H3 headings. Falls back to
    a single overview slide if the document is heading-free."""
    slides: list[Slide] = []
    sections = _split_by_heading(md)

    # Cover slide
    slides.append(Slide(
        slide_id="cover",
        title=capability_name,
        bullets=["How to use this capability",
                 "Generated walkthrough — review with your operator"],
    ))

    for i, (heading, body) in enumerate(sections):
        bullets = _bullet_lines(body)[:5]
        slides.append(Slide(
            slide_id=f"slide_{i + 1}",
            title=heading or f"Section {i + 1}",
            bullets=bullets,
            speaker_notes=body[:400].strip(),
        ))
    if len(slides) == 1:
        # No headings at all — make one content slide from a markdown summary.
        slides.append(Slide(
            slide_id="slide_1",
            title="Walkthrough",
            bullets=_bullet_lines(md)[:6] or [md[:120]],
        ))
    # Wrap-up slide
    slides.append(Slide(
        slide_id="wrap",
        title="What to do next",
        bullets=["Try it on a real task",
                 "Submit feedback so the platform learns from your usage",
                 "Ask the execution assistant if you get stuck"],
    ))
    return slides


def _markdown_to_voice_script(md: str) -> str:
    """Strip markdown formatting to TTS-friendly plain text, one sentence per line."""
    # Strip headings, list bullets, fences.
    cleaned = re.sub(r"^#{1,6}\s+", "", md, flags=re.MULTILINE)
    cleaned = re.sub(r"^[-*]\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"```[\s\S]*?```", "", cleaned)  # drop code blocks
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)  # link text only
    cleaned = re.sub(r"[*_]{1,2}([^*_]+)[*_]{1,2}", r"\1", cleaned)
    # Collapse whitespace, then split on sentence terminators.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    return "\n".join(s.strip() for s in sentences if s.strip())


def _build_storyboard(slides: list[Slide], sentences: list[str], *,
                       capability_id: str) -> list[StoryboardEntry]:
    """Distribute script sentences evenly across slides and estimate timing."""
    if not slides:
        return []
    # Sentence-per-slide distribution (the cover and wrap each get 1, then
    # even split of the rest across content slides).
    content_slides = slides[1:-1] if len(slides) > 2 else slides
    sentence_assignments: dict[str, list[int]] = {s.slide_id: [] for s in slides}
    if sentences:
        if len(content_slides) == 0:
            sentence_assignments[slides[0].slide_id] = list(range(len(sentences)))
        else:
            per_slide = max(1, len(sentences) // len(content_slides))
            for i, sentence_idx in enumerate(range(len(sentences))):
                slide_idx = min(i // per_slide, len(content_slides) - 1)
                sentence_assignments[content_slides[slide_idx].slide_id].append(sentence_idx)
    storyboard: list[StoryboardEntry] = []
    for slide in slides:
        idxs = sentence_assignments.get(slide.slide_id, [])
        words = sum(len(sentences[i].split()) for i in idxs)
        duration = (words / _WORDS_PER_MINUTE) * 60 + _TRANSITION_SECONDS
        # Floor cover / wrap to 3 seconds even when sentence assignment is empty.
        if not idxs:
            duration = max(duration, 3.0)
        storyboard.append(StoryboardEntry(
            slide_id=slide.slide_id,
            script_sentence_indices=idxs,
            estimated_duration_seconds=round(duration, 2),
            suggested_screenshot=None,
        ))
    return storyboard


def _split_by_heading(md: str) -> list[tuple[str, str]]:
    """Split markdown into [(heading, body)] segments."""
    sections: list[tuple[str, str]] = []
    last_pos = 0
    current_heading: str | None = None
    for match in _HEADING_RE.finditer(md):
        if current_heading is not None:
            body = md[last_pos:match.start()].strip()
            sections.append((current_heading, body))
        current_heading = match.group(1).strip()
        last_pos = match.end()
    if current_heading is not None:
        sections.append((current_heading, md[last_pos:].strip()))
    return sections


def _bullet_lines(text: str) -> list[str]:
    """Pull bullet-like lines or split sentences into 1-line summaries."""
    bullets: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("-", "*", "•")):
            bullets.append(line.lstrip("-*• ").strip())
        elif line.endswith(".") and len(line) < 140:
            bullets.append(line.rstrip("."))
    if not bullets:
        # Fallback: take the first 3 short sentences.
        sentences = re.split(r"(?<=[.!?])\s+", text)
        bullets = [s.strip(".") for s in sentences[:3] if 8 < len(s) < 140]
    return bullets
