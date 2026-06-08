"""Word-cloud data + refinement-chip data builder.

Two visualization patterns the templates render:

  1. Word cloud (big, decorative, scannable):
       size      = frequency
       color     = encoding mode (rating | freshness | vetted | sentiment)
       rotation  = age (newer upright, older more rotated)
       link      = URL with the appropriate filter applied

  2. Refinement chips (small, dense, contextual):
       active terms render green; co-occurring terms shown for narrowing.

Both pull from the same `Term` dataclass — templates pick the visual.

Public entry points:
    cloud_for_use_cases(workspace, mode, dimension, current_filters) -> list[Term]
    cloud_for_assets(workspace, mode, category=None, current_filters) -> list[Term]
"""

from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from . import inventory, store, use_cases

LAYER = "product"
PRODUCT = "library"


# ── Term + helpers ──────────────────────────────────────────────────


@dataclass
class Term:
    word: str
    count: int
    size_rem: float = 1.0      # 0.8 – 3.0 for cloud rendering
    color: str = "var(--fg-muted)"
    rotation_deg: float = 0.0  # -8 → 8, more for older items
    sentiment: str = "neutral" # for chip styling
    is_active: bool = False    # term currently in filter
    link: str = "#"
    title: str = ""            # tooltip
    sub_count: int = 0         # auxiliary count (e.g. vetted within group)


# ── Color/size encoders ─────────────────────────────────────────────


def _interp_color(t: float, palette: str) -> str:
    """t in [0, 1]. Returns a hex color along the requested palette."""
    t = max(0.0, min(1.0, t))
    if palette == "rating":
        # red → amber → green
        if t < 0.5:
            r = int(220 - (220 - 180) * (t * 2))
            g = int(53 + (180 - 53) * (t * 2))
            b = 53
        else:
            r = int(180 - 180 * (t - 0.5) * 2)
            g = int(180 - (180 - 127) * (t - 0.5) * 2)
            b = int(53 + (55 - 53) * (t - 0.5) * 2)
        return f"#{r:02x}{g:02x}{b:02x}"
    if palette == "freshness":
        # faded gray → bright emerald
        r = int(120 - 80 * t)
        g = int(120 + 60 * t)
        b = int(120 - 90 * t)
        return f"#{r:02x}{g:02x}{b:02x}"
    if palette == "vetted":
        # gray → green
        r = int(140 - 110 * t)
        g = int(140 + 25 * t)
        b = int(140 - 100 * t)
        return f"#{r:02x}{g:02x}{b:02x}"
    if palette == "sentiment":
        return ("#cf222e" if t < 0.25 else
                  "#9a6700" if t < 0.5 else
                  "#1a7f37" if t < 0.85 else "#0e7a32")
    # frequency neutral
    return "#1a7f37"  # accent-library


def _size_from_count(count: int, max_count: int) -> float:
    """Map count → size_rem (0.85 – 2.6) with log scaling for variety."""
    if max_count <= 1:
        return 1.2
    t = math.log(count + 1) / math.log(max_count + 1)
    return round(0.85 + (2.6 - 0.85) * t, 2)


def _rotation_from_age(age_days: float | None) -> float:
    """Younger → 0deg. Older → up to ±8deg."""
    if age_days is None:
        return 0.0
    if age_days < 1:
        return 0.0
    capped = min(age_days, 60.0)
    # Alternate sign so neighbors don't all lean the same way
    sign = -1 if int(capped) % 2 else 1
    return round(sign * (capped / 60.0) * 8.0, 1)


def _age_days(iso_str: str | None) -> float | None:
    if not iso_str:
        return None
    try:
        t = time.strptime(iso_str[:19], "%Y-%m-%dT%H:%M:%S")
        return max(0.0, (time.time() - time.mktime(t)) / 86400.0)
    except Exception:
        return None


# ── Use-case word source ────────────────────────────────────────────


_PERSONA_STOPWORDS = {"a", "an", "at", "the", "for", "of", "in",
                              "and", "or", "to", "with", "as", "is", "manager"}


# Stopwords for raw text frequency. Curated to be light — we KEEP
# domain-specific terms like RFP, MCP, SaaS, B2B, AI, ML, KPI, SLA.
_KEYWORD_STOPWORDS = {
    # articles / pronouns / conjunctions
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "so",
    "as", "is", "are", "was", "were", "be", "been", "being",
    "i", "you", "he", "she", "it", "we", "they", "them", "their", "his",
    "her", "its", "this", "that", "these", "those", "what", "which", "who",
    "whom", "whose", "where", "when", "why", "how",
    # prepositions / connectives
    "of", "in", "on", "at", "to", "for", "from", "by", "with", "without",
    "into", "onto", "upon", "over", "under", "between", "through", "during",
    "before", "after", "above", "below", "again", "further", "via",
    # common verbs (low signal alone)
    "do", "does", "did", "doing", "done", "have", "has", "had", "having",
    "can", "could", "will", "would", "should", "may", "might", "must",
    "shall", "let", "get", "got", "make", "made", "take", "took", "give",
    "given", "go", "went", "come", "came", "use", "used", "using",
    "see", "saw", "look", "looking", "want", "need", "needed",
    "ensures", "enabling", "allows", "allowing", "include", "includes",
    "including", "ensure", "ensuring", "providing", "provides", "provide",
    "find", "finds", "finding",
    # adjectives & adverbs (low signal)
    "all", "any", "some", "no", "not", "only", "own", "same", "such",
    "than", "too", "very", "much", "many", "more", "most", "few", "less",
    "least", "every", "each", "other", "another", "several", "various",
    # numbers / weak nouns
    "one", "two", "three", "first", "second", "third", "new", "old",
    "good", "bad", "high", "low", "big", "small", "long", "short",
    "early", "late", "next", "last", "now", "today", "tomorrow",
    "yesterday", "year", "years", "month", "months", "day", "days",
    "week", "weeks", "hour", "hours", "minute", "minutes",
    # business filler
    "company", "team", "teams", "people", "user", "users", "person",
    "way", "ways", "thing", "things", "work", "works", "working",
    "process", "processes", "result", "results", "case", "cases",
    "based", "around", "across", "within", "while", "also", "however",
    "step", "steps", "level", "levels", "system", "systems",
    "approach", "approaches", "set", "sets",
    # filler verbs/nouns
    "manage", "manages", "managing", "managed", "management",
    "build", "builds", "building", "built",
    "help", "helps", "helping", "helped",
    "support", "supports", "supporting", "supported",
    "create", "creates", "creating", "created", "creation",
}


def _tokenize_keywords(text: str) -> list[str]:
    """Tokenize for word-frequency. Preserves original casing for display
    (we lowercase the key for dedupe but keep one canonical form).
    Keeps short domain tokens (RFP, AI, MCP, SaaS, KPI, etc.).
    """
    if not text:
        return []
    out: list[str] = []
    # Allow alphanumerics + a few connectors common in tech (/, -)
    import re
    for m in re.findall(r"[A-Za-z][A-Za-z0-9/+-]{1,30}", text):
        low = m.lower()
        if low in _KEYWORD_STOPWORDS:
            continue
        # Drop trailing punctuation / connectors
        cleaned = m.strip("-/+")
        if len(cleaned) < 2:
            continue
        out.append(cleaned)
    return out


def _uc_content_text(uc) -> str:
    """All free-text content from a use case, joined for tokenization."""
    parts = [
        uc.title, uc.summary, uc.persona, uc.industry,
        uc.problem, uc.solution, uc.outcome_metric,
        " ".join(uc.walkthrough or []),
        " ".join(uc.tags or []),
        " ".join((t.get("asset_id") or "") for t in uc.tools_used),
        " ".join((t.get("role") or "") for t in uc.tools_used),
    ]
    return " ".join(p for p in parts if p)


def _persona_keyword(persona: str) -> str:
    """Extract a meaningful role from the persona string.

    'Maya, Senior Sales Engineer at a 200-person SaaS' → 'Sales Engineer'.
    """
    if not persona:
        return ""
    # Drop everything after ' at ' (company info)
    head = persona.split(" at ")[0]
    # Drop name (first comma)
    if "," in head:
        head = head.split(",", 1)[1].strip()
    # Drop adjectives like "Senior", "VP of", etc. — keep last 2 words
    parts = [w for w in head.split() if w.lower() not in _PERSONA_STOPWORDS]
    if len(parts) > 2:
        parts = parts[-2:]
    return " ".join(parts).strip()


def cloud_for_use_cases(workspace: str = "global",
                                mode: str = "frequency",
                                dimension: str = "industry",
                                current: dict[str, str] | None = None,
                                base_url: str = "/library/use-cases",
                                limit: int = 60,
                                viewer_company_id: str | None = None) -> list[Term]:
    """Build a word cloud over `dimension`:
       industry | persona | tool | tag | complexity

    viewer_company_id mirrors use_cases.list_all so the cloud only surfaces
    terms from cases the viewer can actually see — otherwise the "0 use
    cases" hero and a populated cloud disagree.
    """
    current = current or {}
    all_ucs = use_cases.list_all(workspace, viewer_company_id=viewer_company_id)
    counter: Counter = Counter()
    canonical_case: dict[str, str] = {}   # low → display form
    ratings_per: dict[str, list[float]] = {}
    vetted_per: dict[str, int] = {}
    ages_per: dict[str, list[float]] = {}

    for uc in all_ucs:
        keys: list[str] = []
        if dimension == "keyword":
            # True text frequency — tokenize every text field, count words
            for tok in _tokenize_keywords(_uc_content_text(uc)):
                low = tok.lower()
                # Dedupe within a single UC so one UC mentioning "RFP" 5x
                # doesn't dominate the global count.
                if low not in keys:
                    keys.append(low)
                    # Preserve a canonical display form — prefer the
                    # form with the most uppercase letters (so "RFP"
                    # wins over "rfp")
                    cur = canonical_case.get(low, tok)
                    if sum(1 for c in tok if c.isupper()) > sum(1 for c in cur if c.isupper()):
                        canonical_case[low] = tok
                    else:
                        canonical_case.setdefault(low, tok)
        elif dimension == "industry":
            if uc.industry:
                keys.append(uc.industry)
        elif dimension == "persona":
            k = _persona_keyword(uc.persona)
            if k:
                keys.append(k)
        elif dimension == "tool":
            for t in uc.tools_used:
                if t.get("asset_id"):
                    keys.append(t["asset_id"])
        elif dimension == "tag":
            keys.extend([t for t in uc.tags if t])
        elif dimension == "complexity":
            if uc.complexity:
                keys.append(uc.complexity)

        for k in keys:
            counter[k] += 1
            if uc.rating_avg:
                ratings_per.setdefault(k, []).append(uc.rating_avg)
            if uc.vetted:
                vetted_per[k] = vetted_per.get(k, 0) + 1
            a = _age_days(uc.created_at)
            if a is not None:
                ages_per.setdefault(k, []).append(a)

    if not counter:
        return []
    top = counter.most_common(limit)
    max_count = top[0][1]

    out: list[Term] = []
    for word, count in top:
        # Display form — preserves "RFP", "AI", "SaaS" casing for keyword mode
        display = canonical_case.get(word, word) if dimension == "keyword" else word
        ratings = ratings_per.get(word, [])
        ages = ages_per.get(word, [])
        avg_rating = sum(ratings) / len(ratings) if ratings else 0.0
        vetted_ratio = vetted_per.get(word, 0) / count if count else 0.0
        avg_age = sum(ages) / len(ages) if ages else None

        # ── 4-KPI visual encoding (no more user-pickable modes) ──
        # Order: by count desc (already sorted)
        # Size: recency — newer avg age = bigger.
        #   Falls back to frequency-driven sizing when ages are uniform / missing.
        if avg_age is not None and avg_age >= 0:
            freshness_t = max(0.0, min(1.0, 1.0 - avg_age / 30.0))
            size_rem = round(0.85 + (2.6 - 0.85) * freshness_t, 2)
        else:
            size_rem = _size_from_count(count, max_count)
        # Color: avg rating (red→amber→green).
        #   Fallback to a frequency-intensity green when no ratings exist yet.
        if ratings:
            color = _interp_color(avg_rating / 5.0, "rating")
        else:
            t = math.log(count + 1) / math.log(max_count + 1) if max_count > 1 else 0.6
            color = _interp_color(0.30 + 0.55 * t, "frequency")
        # Tilt: vetted ratio (more vetted = more upright).
        #   1.0 vetted → 0deg.  0.0 vetted → ±8deg (sign alternates by hash).
        sign = -1 if (hash(word) & 1) else 1
        rotation_deg = round((1.0 - vetted_ratio) * 6.0 * sign, 1)

        # Active flag — already in current filter for this dimension
        is_active = current.get(dimension) == word

        # Build link toggling this filter
        next_filters = dict(current)
        if is_active:
            next_filters.pop(dimension, None)
        else:
            next_filters[dimension] = word
        next_filters["ws"] = workspace
        query = urlencode({k: v for k, v in next_filters.items() if v})
        link = f"{base_url}?{query}" if query else base_url

        sub = vetted_per.get(word, 0)
        # Tooltip: spell out all 4 KPIs honestly
        title_parts = [f"📊 {count} use case{'' if count == 1 else 's'}"]
        if avg_rating:
            title_parts.append(f"⭐ {avg_rating:.1f} avg rating")
        else:
            title_parts.append("⭐ no ratings yet")
        if avg_age is not None:
            title_parts.append(f"🕒 ~{int(avg_age)}d avg age")
        title_parts.append(f"✓ {int(vetted_ratio*100)}% vetted")
        title = " · ".join(title_parts)

        out.append(Term(
            word=display, count=count,
            size_rem=size_rem,
            color=color,
            rotation_deg=rotation_deg,
            sentiment="positive" if avg_rating >= 4 else
                          "negative" if avg_rating and avg_rating < 3 else "neutral",
            is_active=is_active,
            link=link,
            title=title,
            sub_count=sub,
        ))
    return out


# ── Asset word source ───────────────────────────────────────────────


def cloud_for_assets(workspace: str = "global",
                              mode: str = "frequency",
                              category: str | None = None,
                              current: dict[str, str] | None = None,
                              base_url: str | None = None,
                              limit: int = 60,
                              viewer_company_id: str | None = None) -> list[Term]:
    """Word cloud across asset tags within a category (or all categories).

    Encodes: count, vetted ratio, avg rating, last-enriched age.

    If viewer_company_id is set, rows are pre-filtered via filter_for_company
    so the cloud only surfaces tags from items the viewer can actually see.
    """
    current = current or {}
    base_url = base_url or (f"/library/{category}" if category else "/library")
    cats = [category] if category else [c.key for c in inventory.CATEGORIES]

    counter: Counter = Counter()
    vetted_per: dict[str, int] = {}
    ratings_per: dict[str, list[float]] = {}
    ages_per: dict[str, list[float]] = {}

    for cat in cats:
        rows = inventory.load_category(cat) or []
        if viewer_company_id:
            rows = inventory.filter_for_company(rows, cat, viewer_company_id)
        for row in rows:
            asset_id = row.get("name") or row.get("id") or ""
            if not asset_id:
                continue
            meta = store.get_metadata(workspace, cat, asset_id)
            tags = (row.get("tags") or []) + (meta.tags or [])
            for t in tags:
                if not t or len(t) < 2:
                    continue
                counter[t] += 1
                if meta.vetted:
                    vetted_per[t] = vetted_per.get(t, 0) + 1
                if meta.rating_avg:
                    ratings_per.setdefault(t, []).append(meta.rating_avg)
                a = _age_days(meta.enriched_at)
                if a is not None:
                    ages_per.setdefault(t, []).append(a)

    if not counter:
        return []
    top = counter.most_common(limit)
    max_count = top[0][1]

    out: list[Term] = []
    for word, count in top:
        ratings = ratings_per.get(word, [])
        avg_rating = sum(ratings) / len(ratings) if ratings else 0.0
        vetted_ratio = vetted_per.get(word, 0) / count if count else 0.0
        ages = ages_per.get(word, [])
        avg_age = sum(ages) / len(ages) if ages else None

        if mode == "rating" and ratings:
            color = _interp_color(avg_rating / 5.0, "rating")
        elif mode == "freshness" and avg_age is not None:
            color = _interp_color(max(0.0, 1.0 - avg_age / 30.0), "freshness")
        elif mode == "vetted":
            color = _interp_color(vetted_ratio, "vetted")
        else:
            color = _interp_color(0.55, "frequency")

        is_active = current.get("tag") == word

        next_filters = dict(current)
        if is_active:
            next_filters.pop("tag", None)
        else:
            next_filters["tag"] = word
        next_filters["ws"] = workspace
        query = urlencode({k: v for k, v in next_filters.items() if v})
        link = f"{base_url}?{query}" if query else base_url

        title = f"{count} asset{'' if count == 1 else 's'}"
        if avg_rating:
            title += f" · avg ⭐{avg_rating:.1f}"
        if vetted_ratio:
            title += f" · {int(vetted_ratio*100)}% vetted"

        out.append(Term(
            word=word, count=count,
            size_rem=_size_from_count(count, max_count),
            color=color,
            rotation_deg=_rotation_from_age(avg_age),
            sentiment="positive" if vetted_ratio > 0.7 else "neutral",
            is_active=is_active, link=link, title=title,
            sub_count=vetted_per.get(word, 0),
        ))
    return out


# ── Refinement chips (the second pattern) ──────────────────────────


def refinement_chips_for_use_cases(workspace: str,
                                                current: dict[str, str],
                                                base_url: str = "/library/use-cases",
                                                limit: int = 30,
                                                viewer_company_id: str | None = None) -> list[Term]:
    """Given the current filter set, return co-occurring terms to suggest
    narrowing further. Each chip = a term you can click to add/remove
    from the filter.

    Mixes industries, tools, personas, complexities into one chip row.
    """
    # Start from use cases that match the current filter
    matched = _apply_uc_filters(
        use_cases.list_all(workspace, viewer_company_id=viewer_company_id),
        current,
    )
    counter: Counter = Counter()
    for uc in matched:
        if uc.industry:
            counter[("industry", uc.industry)] += 1
        if uc.complexity:
            counter[("complexity", uc.complexity)] += 1
        for t in uc.tools_used:
            if t.get("asset_id"):
                counter[("tool", t["asset_id"])] += 1
        for tag in uc.tags:
            counter[("tag", tag)] += 1
        p = _persona_keyword(uc.persona)
        if p:
            counter[("persona", p)] += 1

    if not counter:
        return []
    top = counter.most_common(limit)
    max_count = top[0][1]

    out: list[Term] = []
    for (dim, word), count in top:
        is_active = current.get(dim) == word
        next_filters = dict(current)
        if is_active:
            next_filters.pop(dim, None)
        else:
            next_filters[dim] = word
        next_filters["ws"] = workspace
        query = urlencode({k: v for k, v in next_filters.items() if v})
        link = f"{base_url}?{query}" if query else base_url
        out.append(Term(
            word=word, count=count,
            size_rem=_size_from_count(count, max_count) * 0.7 + 0.7,
            color="#1a7f37" if is_active else "#57606a",
            rotation_deg=0.0,
            is_active=is_active, link=link,
            title=f"{dim}: {count} matching",
        ))
    return out


def _apply_uc_filters(ucs, filters):
    out = ucs
    if (ind := filters.get("industry")):
        out = [u for u in out if u.industry == ind]
    if (cx := filters.get("complexity")):
        out = [u for u in out if u.complexity == cx]
    if (tool := filters.get("tool")):
        out = [u for u in out if any(
            t.get("asset_id") == tool for t in u.tools_used
        )]
    if (tag := filters.get("tag")):
        out = [u for u in out if tag in (u.tags or [])]
    if (pers := filters.get("persona")):
        out = [u for u in out
                  if _persona_keyword(u.persona).lower() == pers.lower()]
    if (kw := filters.get("keyword")):
        # Case-insensitive whole-word match anywhere in the UC content
        import re
        pat = re.compile(r"\b" + re.escape(kw) + r"\b", re.I)
        out = [u for u in out if pat.search(_uc_content_text(u))]
    return out


def filter_use_cases(workspace: str,
                            filters: dict[str, str]) -> list[Any]:
    """Apply the (industry/persona/tool/tag/complexity) filters."""
    return _apply_uc_filters(use_cases.list_all(workspace), filters)
