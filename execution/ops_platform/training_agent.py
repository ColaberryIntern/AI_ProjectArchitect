"""Training agent — generates step-by-step walkthroughs (markdown) for workflows.

The walkthrough is the foundation for the embedded onboarding videos: a
narrator script + checklist that a downstream video generator (out of scope
for this iteration) can turn into a screencast. Until then, the markdown
walkthrough itself is rendered as the in-app training panel on the workflow
detail page.

Inputs:
- capability manifest (inputs, outputs, business_value, difficulty)
- one or more historical RunRecord JSONs for the same capability (used to
  show realistic example outputs)

Outputs:
- markdown training script saved to
  output/ops_platform/training/{capability_id}.md
- the manifest's training_video.generated_walkthrough_path is updated to
  point at the new file (in-memory only — the registry caller persists)

The agent uses the LLM when available and falls back to a deterministic
template otherwise.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution import llm_client
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry
from execution.ops_platform.workflow_runner import RunRecord, list_runs

logger = logging.getLogger(__name__)

_TRAINING_DIR = OUTPUT_DIR / "ops_platform" / "training"


@dataclass
class TrainingResult:
    capability_id: str
    output_path: str
    markdown: str
    llm_used: bool


def generate_training(
    capability_id: str,
    *,
    registry: CapabilityRegistry | None = None,
    runs_to_show: int = 2,
) -> TrainingResult:
    """Generate (or regenerate) the training walkthrough for a capability."""
    reg = registry or default_registry()
    capability = reg.get(capability_id)
    if capability is None:
        raise ValueError(f"capability '{capability_id}' is not registered")

    sample_runs = list_runs(capability_id=capability_id, limit=runs_to_show)
    if llm_client.is_available():
        try:
            markdown = _llm_walkthrough(capability, sample_runs)
            llm_used = True
        except Exception as e:
            logger.warning("LLM walkthrough generation failed: %s; falling back", e)
            markdown = _fallback_walkthrough(capability, sample_runs)
            llm_used = False
    else:
        markdown = _fallback_walkthrough(capability, sample_runs)
        llm_used = False

    _TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _TRAINING_DIR / f"{capability_id}.md"
    output_path.write_text(markdown, encoding="utf-8")

    return TrainingResult(
        capability_id=capability_id,
        output_path=str(output_path),
        markdown=markdown,
        llm_used=llm_used,
    )


def get_training_markdown(capability_id: str) -> str | None:
    """Read a previously generated walkthrough. None if not generated yet."""
    path = _TRAINING_DIR / f"{capability_id}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def has_walkthrough(capability_id: str) -> bool:
    """True if a walkthrough has been generated for this capability."""
    return (_TRAINING_DIR / f"{capability_id}.md").exists()


def list_walkthroughs() -> list[str]:
    """Return capability_ids for which a walkthrough exists, sorted."""
    if not _TRAINING_DIR.exists():
        return []
    return sorted(p.stem for p in _TRAINING_DIR.glob("*.md"))


# ── Internal ────────────────────────────────────────────────────────────


_TRAINER_SYSTEM = """You are an Onboarding Trainer for the Colaberry AI Operations
Platform. Given a workflow's metadata and (optionally) some example runs, you
write a clear, friendly markdown walkthrough that a non-technical employee can
follow to use the workflow successfully. Aim for ~500-800 words. Use the
following structure:

1. What this does (one paragraph, plain English)
2. When to use it (bulleted scenarios)
3. What you'll need (inputs)
4. Walkthrough (numbered steps; concrete; one screen-action per step)
5. How to interpret the result (output fields explained)
6. Tips & common pitfalls
7. Where to get help

No mention of MCP, prompts, agents, or LLMs. The reader should feel they are
using an application, not orchestrating one.
"""


def _llm_walkthrough(capability: dict, sample_runs: list[RunRecord]) -> str:
    """Call the LLM to produce the walkthrough."""
    user = _build_trainer_prompt(capability, sample_runs)
    response = llm_client.chat(
        system_prompt=_TRAINER_SYSTEM,
        messages=[{"role": "user", "content": user}],
        temperature=0.4,
        max_tokens=2500,
    )
    md = (response.content or "").strip()
    # If the LLM accidentally wrapped the markdown in a fence, strip it.
    if md.startswith("```"):
        md = md.strip("`")
        if md.lower().startswith("markdown"):
            md = md[len("markdown"):].lstrip()
    return md


def _build_trainer_prompt(capability: dict, runs: list[RunRecord]) -> str:
    parts = [
        f"Generate the training walkthrough for the **{capability.get('name')}** workflow.",
        "",
        f"Type: {capability.get('type')}",
        f"Category: {capability.get('category')} / {capability.get('subcategory', '')}",
        f"Difficulty: {capability.get('difficulty', 'beginner')}",
        f"Description: {capability.get('description', '')}",
        f"Business value: {capability.get('business_value', '')}",
        "",
        "Inputs the user must provide:",
    ]
    for inp in capability.get("inputs", []) or []:
        parts.append(f"- {inp.get('name')} ({inp.get('type')}): {inp.get('description', '')}")
    parts.append("")
    parts.append("Outputs the workflow produces:")
    for out in capability.get("outputs", []) or []:
        parts.append(f"- {out.get('name')} ({out.get('type')}): {out.get('description', '')}")
    parts.append("")
    if runs:
        parts.append("Two real example runs for reference (use them to ground your walkthrough):")
        for r in runs[:2]:
            summary = (r.response or {}).get("summary", "")
            if summary:
                parts.append(f"- Example summary: {summary[:300]}")
    return "\n".join(parts)


def _fallback_walkthrough(capability: dict, sample_runs: list[RunRecord]) -> str:
    """Deterministic walkthrough used when the LLM is unavailable."""
    name = capability.get("name", "this workflow")
    desc = capability.get("description", "")
    bv = capability.get("business_value", "")
    inputs = capability.get("inputs", []) or []
    outputs = capability.get("outputs", []) or []

    lines = [
        f"# {name} — Walkthrough",
        "",
        "## What this does",
        "",
        desc or f"{name} runs a single guided task end-to-end and reports the results.",
        "",
        "## When to use it",
        "",
        f"- {bv}" if bv else "- When you want a reliable, repeatable version of this task.",
        "- When you'd otherwise do this manually and want to save time.",
        "",
        "## What you'll need",
        "",
    ]
    if inputs:
        for inp in inputs:
            req = " (required)" if inp.get("required") else ""
            lines.append(f"- **{inp.get('name')}**{req}: {inp.get('description', '')}")
    else:
        lines.append("- No specific inputs — just click Launch.")
    lines.extend([
        "",
        "## Walkthrough",
        "",
        f"1. Open this workflow's page in the Operations Platform.",
        "2. Fill in each input field above with your own data.",
        "3. Click **Launch** to start the run.",
        "4. Wait for the run to finish — typically under a minute.",
        "5. Review the result panel; the summary at the top explains what changed.",
        "",
        "## How to interpret the result",
        "",
    ])
    if outputs:
        for out in outputs:
            lines.append(f"- **{out.get('name')}**: {out.get('description', '')}")
    else:
        lines.append("- The result panel shows a summary plus structured details for each field of the response.")
    lines.extend([
        "",
        "## Tips & common pitfalls",
        "",
        "- If a field looks empty after the run, the workflow didn't have enough information — add detail to your inputs and re-run.",
        "- The **Feedback** panel on the right is the fastest way to flag a problem; your note becomes searchable for everyone else.",
        "",
        "## Where to get help",
        "",
        "- Use the feedback panel for fast triage.",
        "- For workflow improvements, ping the owner listed at the top of the page.",
    ])
    if sample_runs:
        lines.extend(["", "## Example summary from a recent run", "", "> " + (sample_runs[0].response or {}).get("summary", "")[:500]])
    return "\n".join(lines)
