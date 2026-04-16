"""Business taxonomy registry for AI Advisory.

Deterministic lookup, get, and store for industry taxonomies used by
Step 4 recommendations (Business Outcomes + AI Systems).

Resolution order:
    1. Seeded profiles in industry_profiles.INDUSTRY_PROFILES
    2. On-disk registry at ADVISORY_OUTPUT_DIR/taxonomies/<slug>.json,
       with an alias map at taxonomies/_index.json
    3. Sync LLM generation, persisted for future clients in the same industry

Same industry in → same taxonomy out. The only non-deterministic step is the
first-ever generation for a novel industry; after persist, all future lookups
hit the cache.
"""

import json
import os
import re
import tempfile
from pathlib import Path

from config.settings import ADVISORY_OUTPUT_DIR
from execution.advisory.advisory_state_manager import _safe_replace
from execution.advisory.industry_profiles import INDUSTRY_PROFILES


REQUIRED_FIELDS = (
    "label",
    "aliases",
    "dept_structure",
    "revenue_per_employee",
    "avg_margin",
    "pain_catalog",
    "system_names",
    "agent_roles",
)


def _taxonomies_dir() -> Path:
    return ADVISORY_OUTPUT_DIR / "taxonomies"


def _index_path() -> Path:
    return _taxonomies_dir() / "_index.json"


def _slugify(name: str) -> str:
    """Deterministic slug: lowercase, non-alphanumerics → single hyphen."""
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unknown"


def _load_index() -> dict:
    """Alias → industry_key map. Empty dict if no index yet."""
    path = _index_path()
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _atomic_write_json(target: Path, data) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        _safe_replace(tmp_path, str(target))
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _save_taxonomy(industry_key: str, taxonomy: dict) -> None:
    """Persist a generated taxonomy and update the alias index."""
    _atomic_write_json(_taxonomies_dir() / f"{industry_key}.json", taxonomy)

    index = _load_index()
    index[industry_key] = industry_key
    for alias in taxonomy.get("aliases", []):
        index[_slugify(alias)] = industry_key
    index[_slugify(taxonomy.get("label", ""))] = industry_key
    _atomic_write_json(_index_path(), index)


_SEED_MATCH_THRESHOLD = 5


def _word_boundary_match(needle: str, haystack: str) -> bool:
    """True iff `needle` appears in `haystack` as a whole token (or token sequence).

    Avoids false positives like alias 'retail' hitting 'retailers' or
    'cooperative' hitting 'cooperatively' / 'co-operative-owned'.
    """
    pattern = r"\b" + re.escape(needle.lower().strip()) + r"\b"
    return re.search(pattern, haystack) is not None


def _check_seeded(text: str) -> str | None:
    """Return a seeded industry_id if aliases/label strongly match.

    Scoring:
      - label hit  → +5 (unambiguous, triggers alone)
      - alias hit  → +3 (needs a second signal to trigger)
    Threshold: score >= _SEED_MATCH_THRESHOLD.

    Alias/label matching is word-boundary to prevent generic-word collisions
    (e.g., 'retail' in 'retailers', 'production' in 'small-production').
    """
    text_lower = text.lower()
    best_id, best_score = None, 0
    for industry_id, profile in INDUSTRY_PROFILES.items():
        score = 0
        for alias in profile.get("aliases", []):
            if _word_boundary_match(alias, text_lower):
                score += 3
        if _word_boundary_match(profile["label"], text_lower):
            score += 5
        if score > best_score:
            best_score = score
            best_id = industry_id
    return best_id if best_score >= _SEED_MATCH_THRESHOLD else None


def _check_registry(industry_text: str) -> dict | None:
    """Return a persisted taxonomy if alias or slug matches the free text."""
    index = _load_index()
    if not index:
        return None

    text_lower = industry_text.lower()
    slug_direct = _slugify(industry_text)
    if slug_direct in index:
        return _load_taxonomy_file(index[slug_direct])

    for alias_slug, industry_key in index.items():
        alias_phrase = alias_slug.replace("-", " ")
        if alias_phrase and _word_boundary_match(alias_phrase, text_lower):
            return _load_taxonomy_file(industry_key)
    return None


def _load_taxonomy_file(industry_key: str) -> dict | None:
    path = _taxonomies_dir() / f"{industry_key}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _validate_taxonomy(data: dict) -> None:
    """Raise ValueError if required fields are missing or wrong-typed."""
    if not isinstance(data, dict):
        raise ValueError("taxonomy must be a dict")
    for field in REQUIRED_FIELDS:
        if field not in data:
            raise ValueError(f"taxonomy missing required field: {field}")
    if not isinstance(data["aliases"], list) or not data["aliases"]:
        raise ValueError("aliases must be a non-empty list")
    if not isinstance(data["dept_structure"], dict) or not data["dept_structure"]:
        raise ValueError("dept_structure must be a non-empty dict")
    if not isinstance(data["pain_catalog"], list) or not data["pain_catalog"]:
        raise ValueError("pain_catalog must be a non-empty list")
    if not isinstance(data["system_names"], dict) or not data["system_names"]:
        raise ValueError("system_names must be a non-empty dict")


_GENERATION_SYSTEM_PROMPT = """You are a senior industry analyst. Given a business description, produce a JSON taxonomy of that specific industry — using terminology an operator in that vertical would recognize. Never return generic cross-industry language. If the business spans multiple verticals, pick the primary one.

Return ONLY valid JSON matching this exact schema:

{
  "industry_key": "<kebab-case slug, e.g. 'electric-cooperative'>",
  "label": "<Human-readable industry label>",
  "aliases": ["<synonym>", "<common phrasing>", ...],   // 5-12 strings
  "dept_structure": {
    "<dept_name>": {"pct_of_headcount": <0-1 float>, "avg_fte_cost": <int USD>},
    ...   // 5-8 departments that sum to ~1.0
  },
  "revenue_per_employee": <int USD benchmark>,
  "avg_margin": <0-1 float>,
  "revenue_lift_by_dept": {"<dept>": <0-1 float>, ...},   // optional dept → lift %
  "ai_adoption_rate": <0-1 float>,
  "pain_catalog": [
    {
      "id": "<snake_case>",
      "label": "<industry-specific pain name>",
      "root_cause": "<one sentence>",
      "financial_formula": "<rough cost formula as string>",
      "typical_impact_pct": <0-1 float>
    },
    ...   // 5-7 pains, industry-specific, not generic
  ],
  "system_names": {
    "<dept>": "<Industry-specific AI system name>",
    ...   // one per department that has agent roles
  },
  "agent_roles": {
    "<dept>": [{"name": "AI <specific role>", "role": "<one sentence>"}, ...],
    ...   // 2-5 agents per key department
  }
}

Rules:
- Use department names the user would recognize in their vertical (e.g., "field_services" for utilities, "underwriting" for insurance, "clinical" for healthcare).
- Every system_name and agent name MUST reference the specific industry (e.g., "Grid Operations Engine", not "Operations Engine").
- pain_catalog must describe pains that are characteristic of this industry, not generic business pains.
- Benchmarks should reflect real industry data you are confident about; conservative is better than optimistic.
"""


def _generate_taxonomy(industry_text: str, session_context: str) -> dict:
    """Sync LLM call. Returns validated taxonomy dict."""
    from execution.llm_client import chat

    user_msg = (
        f"Business description and context:\n\n{industry_text}\n\n"
        f"Additional context from discovery answers:\n{session_context[:4000]}\n\n"
        "Produce the industry taxonomy JSON."
    )
    resp = chat(
        system_prompt=_GENERATION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        temperature=0.0,
        max_tokens=3000,
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.content)
    _validate_taxonomy(data)
    return data


def lookup_taxonomy(industry_text: str, session_context: str = "") -> dict:
    """Return a taxonomy for the industry described in `industry_text`.

    Deterministic path:
      1. Match against seeded INDUSTRY_PROFILES (strong alias/label hit)
      2. Match against on-disk registry (alias index)
      3. LLM-generate synchronously, validate, persist, return

    Returned dict always has an extra `_meta` field:
      {"source": "seed"|"registry"|"generated", "industry_key": "<slug>"}
    """
    seeded_id = _check_seeded(industry_text)
    if seeded_id:
        profile = dict(INDUSTRY_PROFILES[seeded_id])
        profile["_meta"] = {"source": "seed", "industry_key": seeded_id}
        return profile

    cached = _check_registry(industry_text)
    if cached:
        cached = dict(cached)
        cached["_meta"] = {
            "source": "registry",
            "industry_key": cached.get("industry_key", _slugify(cached.get("label", ""))),
        }
        return cached

    taxonomy = _generate_taxonomy(industry_text, session_context)
    industry_key = _slugify(taxonomy.get("industry_key") or taxonomy.get("label", ""))
    taxonomy["industry_key"] = industry_key
    _save_taxonomy(industry_key, taxonomy)

    result = dict(taxonomy)
    result["_meta"] = {"source": "generated", "industry_key": industry_key}
    return result
