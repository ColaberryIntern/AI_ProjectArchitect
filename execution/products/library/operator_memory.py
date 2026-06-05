"""Operator memory + shared KB integration for Op 5.

Implements docs/specs/operator-05-operator-memory-system.md (BC todo 9967247849).

Two related but distinct rails:

1. **Shared KB** (admin-controlled, prioritized over learned memory).
   The actual scraping + distribution is owned by Op 1's
   operator_scaffold.scrape_colaberry_knowledge(). This module just
   documents the priority order; the implementation already exists.

2. **Per-operator memory** (this module's main subject).
   Per-user OPERATOR_MEMORY.md committed to the user's workspace repo.
   4 sections:
     - Stated preferences (verbatim quotes from the operator)
     - Recurring patterns (Claude-observed, >= 3 occurrences)
     - Corrections (anti-patterns the operator has explicitly corrected)
     - Open observations (not yet promoted to recurring patterns)

Capture triggers (per spec):
  - Stated preference: "I prefer X" / "always do X" / "from now on, X"
  - Correction: "no, not that" / "don't do X" / "stop doing X"
  - Pattern observation: append to Open observations; promote to
    Recurring patterns after 3 occurrences of the same pattern_key

Priority order (per spec, layer 1 wins on conflict):
    Layer 1: Org CLAUDE.md            (admin -- absolute)
    Layer 2: Shared KB                (admin -- narrative control)
    Layer 3: Tenant CLAUDE.md         (tenant admin)
    Layer 4: Per-user CLAUDE.md       (user preferences)
    Layer 5: OPERATOR_MEMORY.md       (learned -- never overrides above)

Stdlib only.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

OPERATOR_MEMORY_FILENAME = "OPERATOR_MEMORY.md"

# Section markers (used to find + parse + append to the right section)
SECTION_HEADERS = {
    "stated_preferences": "## Stated preferences (verbatim from operator)",
    "recurring_patterns": "## Recurring patterns (Claude-observed, >= 3 occurrences)",
    "corrections": "## Corrections (anti-patterns)",
    "open_observations": "## Open observations (not yet promoted to patterns)",
}

# Promotion threshold: an observation moves from "open" to "recurring" after this many sightings
PATTERN_PROMOTION_THRESHOLD = 3


# ----- Capture trigger detectors --------------------------------------------

# Stated preference: operator explicitly states a preference in 1st person.
# Examples:
#   "I prefer black over autopep8"
#   "From now on, always use uppercase for SQL keywords"
#   "Always send Mandrill emails with BCC to me"
PREFERENCE_PATTERNS = [
    re.compile(r"\bI prefer ([^.\n]{4,200})", re.IGNORECASE),
    re.compile(r"\bfrom now on,?\s*([^.\n]{4,200})", re.IGNORECASE),
    re.compile(r"\balways ([^.\n]{4,200})", re.IGNORECASE),
    re.compile(r"\bnever ([^.\n]{4,200})", re.IGNORECASE),
]

# Correction: operator pushes back on something Claude just did.
# Examples:
#   "no, not that"
#   "don't use em-dashes"
#   "stop doing X"
CORRECTION_PATTERNS = [
    re.compile(r"\b(?:no,?\s+(?:not\s+that|don'?t)|stop (?:doing|using))\s*([^.\n]{4,200})?", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+([^.\n]{4,200})", re.IGNORECASE),
]


@dataclass
class CapturedSignal:
    """One detected signal from a user prompt."""
    kind: str               # 'stated_preference' | 'correction' | 'observation'
    raw_text: str           # the matched substring or the operator's exact phrase
    summary: str            # one-line summary suitable for the memory file
    detected_at: str        # ISO date


def _now_date() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def detect_stated_preference(prompt: str) -> Optional[CapturedSignal]:
    """Detect a stated preference in the operator's prompt. None if no match.

    Returns the FIRST match (conservative -- one signal per prompt).
    """
    for pattern in PREFERENCE_PATTERNS:
        m = pattern.search(prompt)
        if not m:
            continue
        raw = m.group(0).strip()
        return CapturedSignal(
            kind="stated_preference",
            raw_text=raw,
            summary=f'"{raw}"',
            detected_at=_now_date(),
        )
    return None


def detect_correction(prompt: str) -> Optional[CapturedSignal]:
    """Detect a correction. None if no match."""
    for pattern in CORRECTION_PATTERNS:
        m = pattern.search(prompt)
        if not m:
            continue
        raw = m.group(0).strip()
        return CapturedSignal(
            kind="correction",
            raw_text=raw,
            summary=f'"{raw}"',
            detected_at=_now_date(),
        )
    return None


def detect_pattern_observation(behavior_description: str) -> CapturedSignal:
    """Claude-observed behavior (not user-driven). Always returns a signal.

    Caller passes a description of what Claude noticed (e.g. "operator ran
    Playwright smoke check after deploy"). Promotion to Recurring patterns
    happens via promote_pattern_if_observed() after 3 occurrences.
    """
    return CapturedSignal(
        kind="observation",
        raw_text=behavior_description.strip(),
        summary=behavior_description.strip(),
        detected_at=_now_date(),
    )


# ----- File I/O helpers ----------------------------------------------------

def _memory_path(workspace_dir: Path) -> Path:
    return workspace_dir / OPERATOR_MEMORY_FILENAME


def render_starter_operator_memory(user_email: str, user_display_name: str) -> str:
    """Produce the starter OPERATOR_MEMORY.md text seeded at workspace creation.

    Mirrors the template in the spec exactly. Capture writers append below the
    section headers; they do not rewrite them.
    """
    today = _now_date()
    return f"""# Operator memory - {user_display_name}

Email: **{user_email}**
First seen: {today}
Sessions captured: 0

> This file is **Layer 5 (lowest priority)** in the assembled context Claude Code
> reads at session start. The priority order is:
>
> 1. Org CLAUDE.md (admin-controlled, absolute)
> 2. Shared KB ({user_display_name}'s company-wide knowledge base from www.colaberry.com / www.colaberry.ai / www.enterprise.colaberry.com)
> 3. Tenant CLAUDE.md (tenant-admin policy, optional)
> 4. Per-user CLAUDE.md (your own preferences)
> 5. **This file** (what I have learned about you over time)
>
> Anything here is overridable by layers 1-4. The shared KB and org policy
> always win on conflict. This file is admin-readable for support / audit.

---

{SECTION_HEADERS["stated_preferences"]}

(Verbatim quotes from you, captured when you say "I prefer X", "always do X",
"from now on X", "never X". Append-only.)

---

{SECTION_HEADERS["recurring_patterns"]}

(Behaviors I have observed you do at least 3 times. Promoted from Open observations
automatically once the count hits 3. These shape my default behavior.)

---

{SECTION_HEADERS["corrections"]}

(Things you have explicitly told me to stop doing. Append-only -- I do not edit
or remove your corrections without your explicit say-so.)

---

{SECTION_HEADERS["open_observations"]}

(Behaviors I have noticed but not yet seen 3 times. Promoted to Recurring patterns
once the count hits 3, or aged out after 30 days of no further sightings.)

---

*Seeded by Op 5 (operator memory) on {today}.*
"""


def read_memory_file(workspace_dir: Path) -> Optional[str]:
    """Read the raw OPERATOR_MEMORY.md text. None if file doesn't exist."""
    p = _memory_path(workspace_dir)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def append_memory_entry(
    workspace_dir: Path,
    signal: CapturedSignal,
) -> dict:
    """Append a captured signal under the appropriate section header.

    Idempotent: if the same `raw_text` already appears in the target section,
    skip. Returns a manifest {action, section, path}.
    """
    p = _memory_path(workspace_dir)
    if not p.exists():
        return {"action": "failed", "reason": "memory file not provisioned"}

    kind_to_section = {
        "stated_preference": "stated_preferences",
        "correction": "corrections",
        "observation": "open_observations",
    }
    section_key = kind_to_section.get(signal.kind)
    if section_key is None:
        return {"action": "failed", "reason": f"unknown signal kind: {signal.kind}"}

    section_header = SECTION_HEADERS[section_key]
    current = p.read_text(encoding="utf-8")

    # Idempotency: refuse to re-write the same raw_text under the same section
    if signal.raw_text in current:
        return {"action": "deduped", "section": section_key, "path": str(p)}

    new_line = f"- {signal.detected_at} -- {signal.summary}\n"
    if section_header in current:
        # Insert immediately after the section header's "(...)" descriptor line.
        # Pattern: section_header + "\n\n" + descriptor + "\n\n" + first entry...
        # We find section_header position and the next "---" separator below it.
        idx = current.index(section_header)
        # Find next "---" after this section header
        rest = current[idx + len(section_header):]
        end_marker_offset = rest.find("\n---\n")
        if end_marker_offset == -1:
            # No following marker -- append at end of file as a safety net
            updated = current + "\n" + new_line
        else:
            insert_at = idx + len(section_header) + end_marker_offset
            # Insert the new line just before the closing "---"
            updated = current[:insert_at] + "\n" + new_line + current[insert_at:]
        p.write_text(updated, encoding="utf-8")
        return {"action": "appended", "section": section_key, "path": str(p)}
    return {"action": "failed", "reason": f"section header not found: {section_header}"}


def promote_pattern_if_observed(
    workspace_dir: Path,
    pattern_key: str,
) -> dict:
    """Count occurrences of `pattern_key` in the Open observations section.

    If count >= PATTERN_PROMOTION_THRESHOLD, move it (append the line) to the
    Recurring patterns section. Returns a manifest {action, count, path}.
    """
    p = _memory_path(workspace_dir)
    if not p.exists():
        return {"action": "failed", "reason": "memory file not provisioned"}

    text = p.read_text(encoding="utf-8")
    open_header = SECTION_HEADERS["open_observations"]
    if open_header not in text:
        return {"action": "failed", "reason": "open observations section not found"}

    open_section_start = text.index(open_header) + len(open_header)
    rest = text[open_section_start:]
    open_section_end_rel = rest.find("\n---\n")
    if open_section_end_rel == -1:
        open_section_text = rest
    else:
        open_section_text = rest[:open_section_end_rel]

    count = open_section_text.lower().count(pattern_key.lower())
    if count < PATTERN_PROMOTION_THRESHOLD:
        return {"action": "below_threshold", "count": count, "path": str(p)}

    # Build a recurring-patterns entry
    today = _now_date()
    promoted_line = f"- {today} -- {pattern_key} (observed {count}x; promoted from Open observations)\n"

    # Insert into Recurring patterns section
    rp_header = SECTION_HEADERS["recurring_patterns"]
    if rp_header not in text:
        return {"action": "failed", "reason": "recurring patterns section not found"}
    rp_idx = text.index(rp_header)
    rp_rest = text[rp_idx + len(rp_header):]
    rp_end_rel = rp_rest.find("\n---\n")
    if rp_end_rel == -1:
        updated = text + "\n" + promoted_line
    else:
        insert_at = rp_idx + len(rp_header) + rp_end_rel
        updated = text[:insert_at] + "\n" + promoted_line + text[insert_at:]

    p.write_text(updated, encoding="utf-8")
    return {"action": "promoted", "count": count, "path": str(p), "promoted_text": promoted_line.strip()}
