"""Deterministic citation injector for chapter text.

The chapter writer asks the LLM to cite each linked Requirement as
``[REQ-NNN]`` inline, but compliance is inconsistent — gpt-4o-mini and
similar models treat the citation rule as advisory and often ignore it,
especially after a retry. This module guarantees citation presence by
post-processing chapter text: it finds the first plausible mention of
each linked Requirement (by name keyword, action verb phrase, or actor)
and inserts a bracketed REQ ID inline. AC IDs are inserted similarly
when the AC's `then` clause has a distinctive keyword.

Design rules:
- One citation per Requirement minimum (the Completeness gate's
  threshold). A second mention of the same Requirement is left alone.
- Insert as a bracketed token immediately after the matched word/phrase,
  with a space before. Never break sentence punctuation.
- Skip injection inside fenced code blocks, inline code, and existing
  bracket citations. Code blocks must stay verbatim.
- Idempotent: running the injector twice on the same text produces the
  same output.

Returns the modified text and a report describing which Requirements
were already cited, which were injected, and which could not be matched
(in which case the caller may decide to surface a warning).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class InjectionReport:
    """Result of running the citation injector on a chapter."""

    already_cited: list[str] = field(default_factory=list)
    injected: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    ac_already_cited: list[str] = field(default_factory=list)
    ac_injected: list[str] = field(default_factory=list)


def _strip_code_blocks(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Return (text-with-code-blocks-replaced-by-placeholders, ranges).

    We replace fenced code (``` ... ```) and inline code (`...`) with a
    placeholder of identical length so character offsets line up. The
    caller restores them after injection.
    """
    ranges: list[tuple[int, int]] = []
    out = list(text)

    # Fenced code blocks: ```...```
    for m in re.finditer(r"```[\s\S]*?```", text, re.MULTILINE):
        ranges.append((m.start(), m.end()))
        for i in range(m.start(), m.end()):
            out[i] = "\0"  # NUL placeholder, stays out of normal regex hits

    # Inline code: `...` — only one-line, not crossing newlines
    for m in re.finditer(r"`[^`\n]+`", text):
        # Avoid double-mark inside a fenced block
        in_fenced = any(s <= m.start() < e for s, e in ranges)
        if in_fenced:
            continue
        ranges.append((m.start(), m.end()))
        for i in range(m.start(), m.end()):
            out[i] = "\0"

    return "".join(out), ranges


def _restore_code_blocks(stripped: str, original: str,
                         ranges: list[tuple[int, int]]) -> str:
    """Restore code blocks at their original offsets after edits.

    The injector only ever inserts characters; it never deletes. So
    a single offset map (cumulative shift at each position) is enough.
    But our injection works on the stripped string and we do not edit
    inside placeholder regions. We rebuild by walking the stripped
    string, copying each char unless it's NUL — at which point we copy
    the corresponding original byte.
    """
    out: list[str] = []
    o_idx = 0
    for ch in stripped:
        if ch == "\0":
            out.append(original[o_idx])
            o_idx += 1
        else:
            # If the original at o_idx is also a non-NUL char (i.e., not in a
            # protected region), advance the original index too.
            if o_idx < len(original) and original[o_idx] != "\0":
                # Detect inserted characters: stripped is longer than original
                # in places where we inserted. Match the chars one by one.
                if ch == original[o_idx]:
                    out.append(ch)
                    o_idx += 1
                else:
                    # This is an inserted character (citation token). Emit it
                    # but don't advance o_idx.
                    out.append(ch)
            else:
                out.append(ch)
    # Append any remaining original (shouldn't happen because we copy NULs)
    return "".join(out)


def _build_citation_keywords(requirement: dict) -> list[str]:
    """Return a prioritized list of keyword phrases to search for.

    Order matters: the first phrase that matches in the chapter wins
    the injection. We try multi-word phrases first (more specific) and
    fall back to single keywords (more recall but riskier).
    """
    keywords: list[str] = []

    # 1. Multi-word phrases derived from the action ("upload a BOL PDF")
    action = (requirement.get("action") or "").strip()
    if action:
        # Take first 4-6 words of the action
        action_words = action.split()
        if len(action_words) >= 3:
            keywords.append(" ".join(action_words[:4]))
        keywords.append(action)

    # 2. The Requirement's name verbatim ("BOL Document Ingestion")
    name = (requirement.get("name") or "").strip()
    if name:
        keywords.append(name)

    # 3. Distinctive single keywords from the name (capitalized words ≥ 4 chars)
    if name:
        single = [w for w in re.findall(r"\b[A-Z][a-zA-Z]{3,}\b", name)]
        keywords.extend(single)

    # 4. problem_mapped_to as a slug-style hint (last resort, often
    #    matches the section title)
    pmt = (requirement.get("problem_mapped_to") or "").strip()
    if pmt:
        keywords.append(pmt.replace("_", " "))

    return [k for k in keywords if len(k) >= 3]


def _line_at(text: str, pos: int) -> str:
    """Return the full line of text containing position ``pos``."""
    line_start = text.rfind("\n", 0, pos) + 1
    line_end = text.find("\n", pos)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end]


def _is_inside_quotes(text: str, start: int, end: int) -> bool:
    """Return True if the matched range falls inside paired ' or " on
    the same line. Crude but effective — we just count quote chars
    before the match on its line and check parity.
    """
    line_start = text.rfind("\n", 0, start) + 1
    prefix_on_line = text[line_start:start]
    # Treat single and double quotes independently
    for quote_char in ("'", '"'):
        # Skip apostrophes that are clearly contractions (letter + apostrophe + letter)
        # by counting only "real" quote occurrences. For pragmatism, just count.
        count = prefix_on_line.count(quote_char)
        # If there's an odd number of this quote char before the match,
        # the match is between an opening and a future-closing quote.
        if count % 2 == 1:
            # Verify there's a closing quote somewhere after on the same line
            line_end = text.find("\n", end)
            if line_end == -1:
                line_end = len(text)
            if quote_char in text[end:line_end]:
                return True
    return False


def _find_first_match_outside_brackets(
    text: str, phrase: str
) -> int | None:
    """Locate the first match of ``phrase`` (case-insensitive) that is
    NOT already followed by an existing ``[REQ-…]`` citation, NOT inside
    a NUL-placeholder region, NOT inside an existing bracket, NOT inside
    a markdown heading line, and NOT inside a quoted string literal.

    Returns the char index just past the matched phrase (i.e., where to
    insert the bracketed citation), or None if no good match exists.
    """
    pattern = re.compile(re.escape(phrase), re.IGNORECASE)
    for m in pattern.finditer(text):
        start, end = m.start(), m.end()
        # Skip if inside a code-block placeholder
        if "\0" in text[start:end]:
            continue
        # Skip if the next 12 chars already contain [REQ- (already cited)
        tail = text[end:end + 12]
        if re.match(r"\s*\[REQ-\d", tail):
            continue
        # Skip if we're inside an existing bracket [...]
        # crude check: look back for [ without a closing ]
        prefix = text[max(0, start - 80):start]
        if "[" in prefix and "]" not in prefix.split("[")[-1]:
            continue
        # Skip if the match is on a markdown heading line ("# ...", "## ...", etc.)
        line = _line_at(text, start).lstrip()
        if line.startswith("#"):
            continue
        # Skip if inside a paired quoted string on the same line
        if _is_inside_quotes(text, start, end):
            continue
        return end
    return None


def _ac_keyword(ac: dict) -> str | None:
    """Pick a distinctive keyword from an AC's `then` clause.

    Heuristic: the first capitalized identifier or the first quoted
    string in the `then` clause is usually highly specific (e.g.
    'INV-77', 'p95 latency', 'detention'). We use that as the
    insertion anchor.
    """
    then = (ac.get("then") or "").strip()
    if not then:
        return None
    # Prefer quoted single tokens
    q = re.search(r"'([^']{2,40})'", then)
    if q:
        return q.group(1)
    # Capitalized identifiers like INV-77, SHP-1234, REQ-001 (but not REQ-)
    cap = re.search(r"\b([A-Z]{2,}-\d+)\b", then)
    if cap:
        return cap.group(1)
    # First number with units
    num = re.search(r"\b(\d+\s*(?:ms|s|seconds|%|rps))\b", then)
    if num:
        return num.group(1)
    return None


def inject_citations(
    text: str,
    linked_requirements: list[dict],
) -> tuple[str, InjectionReport]:
    """Inject [REQ-NNN] (and optional [AC-NNN-N]) citations into chapter text.

    Args:
        text: Original chapter markdown body.
        linked_requirements: Requirements traced to this chapter.

    Returns:
        (modified_text, report). ``report`` lists which Requirements
        were already cited, which were injected, and which could not
        be matched.
    """
    if not linked_requirements:
        return text, InjectionReport()

    report = InjectionReport()
    stripped, _ = _strip_code_blocks(text)

    # We accumulate edits as (offset, insert_text) pairs and apply them
    # all at the end in reverse order so offsets stay valid.
    edits: list[tuple[int, str]] = []

    for req in linked_requirements:
        rid = req.get("id")
        if not rid:
            continue

        # Already cited?
        if re.search(rf"\[{re.escape(rid)}\]", text):
            report.already_cited.append(rid)
            # Try to add an AC citation if the chapter doesn't already
            # have one for this requirement's first AC.
            for ac in (req.get("acceptance_criteria") or [])[:1]:
                ac_id = ac.get("id")
                if not ac_id:
                    continue
                if re.search(rf"\[{re.escape(ac_id)}\]", text):
                    report.ac_already_cited.append(ac_id)
                    continue
                anchor = _ac_keyword(ac)
                if not anchor:
                    continue
                pos = _find_first_match_outside_brackets(stripped, anchor)
                if pos is not None:
                    edits.append((pos, f" [{ac_id}]"))
                    report.ac_injected.append(ac_id)
            continue

        # Not cited — find a place to inject
        injected = False
        for kw in _build_citation_keywords(req):
            pos = _find_first_match_outside_brackets(stripped, kw)
            if pos is not None:
                edits.append((pos, f" [{rid}]"))
                report.injected.append(rid)
                injected = True
                # Also try to inject the first AC if measurable
                for ac in (req.get("acceptance_criteria") or [])[:1]:
                    ac_id = ac.get("id")
                    if not ac_id:
                        continue
                    if re.search(rf"\[{re.escape(ac_id)}\]", text):
                        report.ac_already_cited.append(ac_id)
                        break
                    anchor = _ac_keyword(ac)
                    if not anchor:
                        break
                    ac_pos = _find_first_match_outside_brackets(stripped, anchor)
                    if ac_pos is not None and ac_pos != pos:
                        edits.append((ac_pos, f" [{ac_id}]"))
                        report.ac_injected.append(ac_id)
                break

        if not injected:
            report.unmatched.append(rid)

    # Apply edits in reverse order so offsets stay valid.
    edits.sort(key=lambda e: e[0], reverse=True)
    out = text
    for offset, ins in edits:
        out = out[:offset] + ins + out[offset:]

    return out, report
