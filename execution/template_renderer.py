"""Markdown template rendering with simple placeholder substitution.

Uses {{placeholder}} syntax for single values and {{#list}}...{{/list}}
for repeated sections. Keeps dependencies minimal — no template engine required.
"""

import re
from pathlib import Path

from config.settings import TEMPLATES_DIR


def load_template(template_name: str) -> str:
    """Load a template file by name.

    Args:
        template_name: Filename of the template (e.g., 'outline_template.md').

    Returns:
        The template content as a string.

    Raises:
        FileNotFoundError: If the template does not exist.
    """
    path = TEMPLATES_DIR / template_name
    return path.read_text(encoding="utf-8")


def render_template(template: str, context: dict) -> str:
    """Replace {{placeholders}} in a template with values from context.

    Handles:
    - Simple values: {{key}} -> context["key"]
    - Missing keys: left as-is

    Args:
        template: The template string with {{placeholders}}.
        context: Dict of key-value pairs for substitution.

    Returns:
        The rendered string.
    """
    result = template
    for key, value in context.items():
        if isinstance(value, (str, int, float)):
            result = result.replace("{{" + key + "}}", str(value))
    return result


def render_list_sections(template: str, list_key: str, items: list[dict]) -> str:
    """Render repeated sections in a template.

    Handles {{#key}}...{{/key}} blocks by repeating the inner content
    for each item in the list.

    Args:
        template: The template string.
        list_key: The key for the list section (e.g., 'sections').
        items: List of dicts, each providing context for one repetition.

    Returns:
        The rendered string.
    """
    pattern = re.compile(
        r"\{\{#" + re.escape(list_key) + r"\}\}(.*?)\{\{/" + re.escape(list_key) + r"\}\}",
        re.DOTALL,
    )
    match = pattern.search(template)
    if not match:
        return template

    inner_template = match.group(1)
    rendered_items = []
    for item in items:
        rendered = inner_template
        for key, value in item.items():
            if isinstance(value, (str, int, float)):
                rendered = rendered.replace("{{" + key + "}}", str(value))
        rendered_items.append(rendered)

    return template[: match.start()] + "".join(rendered_items) + template[match.end() :]


def render_outline(project_name: str, version: int, sections: list[dict]) -> str:
    """Render an outline document from template.

    Args:
        project_name: The project name.
        version: Outline version number.
        sections: List of section dicts with index, title, summary.

    Returns:
        Rendered Markdown string.
    """
    template = load_template("outline_template.md")
    result = render_template(template, {"project_name": project_name, "version": version})
    result = render_list_sections(result, "sections", sections)
    return result


def render_chapter(index: int, title: str, purpose: str, design_intent: str, implementation_guidance: str) -> str:
    """Render a chapter document from template.

    Args:
        index: Chapter number.
        title: Chapter title.
        purpose: Purpose section content.
        design_intent: Design intent section content.
        implementation_guidance: Implementation guidance section content.

    Returns:
        Rendered Markdown string.
    """
    template = load_template("chapter_template.md")
    return render_template(
        template,
        {
            "index": index,
            "title": title,
            "purpose": purpose,
            "design_intent": design_intent,
            "implementation_guidance": implementation_guidance,
        },
    )


def render_chapter_enterprise(index: int, title: str, content: str) -> str:
    """Render an enterprise chapter from template.

    Enterprise chapters use a single 'content' field containing full markdown
    body with subsection headings, rather than the legacy 3-field format.

    Args:
        index: Chapter number.
        title: Chapter title.
        content: Full markdown body (includes ## subsection headings).

    Returns:
        Rendered Markdown string.
    """
    template = load_template("enterprise_chapter_template.md")
    return render_template(
        template,
        {
            "index": index,
            "title": title,
            "content": content,
        },
    )


def render_final_document(
    project_name: str, version: str, date: str, status: str, chapters_content: str
) -> str:
    """Render the final assembled document from template.

    Args:
        project_name: The project name.
        version: Document version string (e.g., 'v1').
        date: Assembly date string.
        status: Document status (e.g., 'Final').
        chapters_content: The compiled chapters as a single string.

    Returns:
        Rendered Markdown string.
    """
    template = load_template("final_document_template.md")
    return render_template(
        template,
        {
            "project_name": project_name,
            "version": version,
            "date": date,
            "status": status,
            "chapters_content": chapters_content,
        },
    )


def render_quality_report(project_name: str, date: str, overall_result: str, gate_results: str) -> str:
    """Render a quality report from template.

    Args:
        project_name: The project name.
        date: Report date.
        overall_result: 'PASS' or 'FAIL'.
        gate_results: Formatted gate results string.

    Returns:
        Rendered Markdown string.
    """
    template = load_template("quality_report_template.md")
    return render_template(
        template,
        {
            "project_name": project_name,
            "date": date,
            "overall_result": overall_result,
            "gate_results": gate_results,
        },
    )
