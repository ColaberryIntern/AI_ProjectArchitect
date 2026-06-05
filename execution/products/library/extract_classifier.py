"""Heuristic classifier: given text (one task, or a whole list rolled up),
suggest which Colaberry library output_types it could be extracted into.

Keyword/regex based -- fast, deterministic, no LLM dependency. False positives
are OK (user picks the right tag); false negatives are the cost (the user
doesn't see the suggestion). The signals lean inclusive.

Phase 7 of the onboarding pilot. Drives the SUGGESTED column on
/my-day/?view=extract.

Output_type -> visual color (matches Bootstrap-ish palette already used
elsewhere in the app):

    skill        green       reusable code/function
    agent        purple      autonomous orchestration
    prompt       blue        LLM call template
    mcp          teal        MCP server / model context protocol
    capability   indigo      domain expertise
    template     gray        starter / boilerplate
    directive    amber       SOP / runbook / policy guidance
    policy       red         governance rule / compliance
    scorecard    orange      metrics / KPI / measure
    eval         pink        test / benchmark / quality check
    report       slate       dashboard / status update
    connector    cyan        API / integration / webhook
    adapter      lime        bridge / converter
    cron         brown       schedule / recurring
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class OutputTypeMeta:
    key: str
    label: str
    color_bg: str    # CSS background color for the tag
    color_fg: str    # CSS text color
    description: str


OUTPUT_TYPES: dict[str, OutputTypeMeta] = {
    "skill":      OutputTypeMeta("skill",      "Skill",      "#dafbe1", "#15803d",
                                                          "reusable code or helper function"),
    "agent":      OutputTypeMeta("agent",      "Agent",      "#ede9fe", "#5b21b6",
                                                          "autonomous multi-step orchestration"),
    "prompt":     OutputTypeMeta("prompt",     "Prompt",     "#dbeafe", "#1e40af",
                                                          "LLM call template"),
    "mcp":        OutputTypeMeta("mcp",        "MCP",        "#ccfbf1", "#115e59",
                                                          "Model Context Protocol server"),
    "capability": OutputTypeMeta("capability", "Capability", "#e0e7ff", "#3730a3",
                                                          "domain expertise bundle"),
    "template":   OutputTypeMeta("template",   "Template",   "#e5e7eb", "#374151",
                                                          "starter / boilerplate"),
    "directive":  OutputTypeMeta("directive",  "Directive",  "#fef3c7", "#92400e",
                                                          "SOP / runbook / how-to"),
    "policy":     OutputTypeMeta("policy",     "Policy",     "#fee2e2", "#991b1b",
                                                          "governance rule / compliance"),
    "scorecard":  OutputTypeMeta("scorecard",  "Scorecard",  "#ffedd5", "#9a3412",
                                                          "metric / KPI / rubric"),
    "eval":       OutputTypeMeta("eval",       "Eval",       "#fce7f3", "#9d174d",
                                                          "test / benchmark / quality gate"),
    "report":     OutputTypeMeta("report",     "Report",     "#e2e8f0", "#334155",
                                                          "dashboard / status update / recap"),
    "connector":  OutputTypeMeta("connector",  "Connector",  "#cffafe", "#155e75",
                                                          "API / integration / webhook"),
    "adapter":    OutputTypeMeta("adapter",    "Adapter",    "#ecfccb", "#3f6212",
                                                          "bridge / translator / converter"),
    "cron":       OutputTypeMeta("cron",       "Cron",       "#fef2cd", "#713f12",
                                                          "schedule / recurring job"),
}


# Per-type regex patterns. Inclusive on purpose -- duplicate matches are
# deduped at the end and the user gets to pick. All patterns are case-
# insensitive and word-boundary anchored where reasonable.
_PATTERNS: dict[str, list[re.Pattern]] = {
    "skill": [re.compile(r"\b(reusable|helper|utility|util\b|wrapper|function|library code|module|sdk)\b", re.I)],
    "agent": [re.compile(r"\b(agent|autonomous|orchestrat\w*|multi[-\s]?step|chain[-\s]?of[-\s]?thought)\b", re.I)],
    "prompt": [re.compile(r"\b(prompt template|few[-\s]?shot|system prompt|user prompt|prompt engineering)\b", re.I)],
    "mcp": [re.compile(r"\b(MCP|model context protocol|claude[-\s]?desktop|mcp server)\b", re.I)],
    "capability": [re.compile(r"\b(capability|capabilit\w+|expertise|domain knowledge|knowledge base)\b", re.I)],
    "template": [re.compile(r"\b(template|boilerplate|starter|scaffold|skeleton|seed file)\b", re.I)],
    "directive": [re.compile(r"\b(SOP|runbook|how[-\s]?to|protocol|procedure|guideline|playbook|walkthrough)\b", re.I)],
    "policy": [re.compile(r"\b(policy|policies|rule|compliance|governance|must comply|approved|forbidden|prohibit\w*)\b", re.I)],
    "scorecard": [re.compile(r"\b(scorecard|metric|metrics|KPI|measure\w*|grade|rubric|benchmark target|score\b)\b", re.I)],
    "eval": [re.compile(r"\b(eval|evaluation|test suite|benchmark|quality check|accuracy|assess\w*|regression test)\b", re.I)],
    "report": [re.compile(r"\b(report|reporting|dashboard|status update|weekly recap|summary email|status report)\b", re.I)],
    "connector": [re.compile(r"\b(connector|integration|API\b|webhook|sync\b|REST|GraphQL|endpoint)\b", re.I)],
    "adapter": [re.compile(r"\b(adapter|bridge\b|converter|translator|transform\w*|normalizer|wrapper for)\b", re.I)],
    "cron": [re.compile(r"\b(cron|schedule|daily|hourly|weekly|every\s+\d+|recurring|nightly|midnight|UTC\b)\b", re.I)],
}


def suggest_output_types(text: str, *, max_tags: int = 4) -> list[str]:
    """Return a list of output_type keys (subset of OUTPUT_TYPES.keys()) that
    the given text plausibly maps to.

    Tags are returned in stable priority order (the OUTPUT_TYPES dict order)
    rather than match order, so identical text always produces identical tags.

    `max_tags` caps the noise -- 4 is enough to surface real options without
    overwhelming a table row. Pass 0 for unlimited.
    """
    if not text:
        return []
    found: list[str] = []
    for ot_key in OUTPUT_TYPES:
        patterns = _PATTERNS.get(ot_key, [])
        for p in patterns:
            if p.search(text):
                found.append(ot_key)
                break
    if max_tags and len(found) > max_tags:
        found = found[:max_tags]
    return found


def suggest_for_list(list_name: str, task_titles: Iterable[str],
                              task_descriptions: Iterable[str] = ()) -> list[str]:
    """Roll up suggestions for a whole BC list.

    Concatenates the list name + all task titles + all descriptions into one
    string and runs the classifier on it. Catches signals that no single task
    holds but the list collectively expresses (e.g. one task says "cron",
    another says "API", together they suggest connector+cron).
    """
    parts: list[str] = [list_name or ""]
    parts.extend(t for t in task_titles if t)
    parts.extend(d for d in task_descriptions if d)
    return suggest_output_types(" ".join(parts))


def tag_color(output_type: str) -> tuple[str, str]:
    """Return (bg, fg) hex colors for the tag chip. Falls back to neutral grey
    for unknown types so a new output_type added to a template but missing from
    OUTPUT_TYPES still renders without crashing.
    """
    meta = OUTPUT_TYPES.get(output_type)
    if meta:
        return meta.color_bg, meta.color_fg
    return "#f1f5f9", "#475569"


def label_for(output_type: str) -> str:
    meta = OUTPUT_TYPES.get(output_type)
    return meta.label if meta else output_type.title()
