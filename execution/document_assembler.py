"""Final document compilation and assembly.

Compiles approved chapters into a single coherent document,
applies formatting, naming conventions, and version tagging.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR


def compile_document(chapter_paths: list[str], chapter_titles: list[str]) -> str:
    """Read all approved chapter files and compile into a single document.

    Chapters are compiled in the order provided (which must match outline order).
    No content is rewritten — this is a mechanical assembly.

    Args:
        chapter_paths: Ordered list of paths to chapter Markdown files.
        chapter_titles: Ordered list of chapter titles for headers.

    Returns:
        The compiled document as a single string.

    Raises:
        FileNotFoundError: If a chapter file is missing.
    """
    parts = []
    for i, (path, title) in enumerate(zip(chapter_paths, chapter_titles)):
        chapter_path = Path(path)
        if not chapter_path.exists():
            raise FileNotFoundError(f"Chapter file not found: {path}")

        content = chapter_path.read_text(encoding="utf-8")
        parts.append(content)

        # Add separator between chapters (not after the last one)
        if i < len(chapter_paths) - 1:
            parts.append("\n\n---\n\n")

    return "".join(parts)


def apply_formatting(document: str) -> str:
    """Standardize formatting across the compiled document.

    - Ensures consistent heading hierarchy
    - Normalizes spacing between sections
    - Removes duplicate blank lines

    Args:
        document: The raw compiled document.

    Returns:
        The formatted document.
    """
    # Normalize line endings
    doc = document.replace("\r\n", "\n")

    # Remove triple+ blank lines (keep max 2 newlines between sections)
    doc = re.sub(r"\n{4,}", "\n\n\n", doc)

    # Ensure headings have blank line before them (unless start of doc)
    doc = re.sub(r"([^\n])\n(#{1,6}\s)", r"\1\n\n\2", doc)

    # Remove trailing whitespace on each line
    lines = [line.rstrip() for line in doc.split("\n")]
    doc = "\n".join(lines)

    # Ensure document ends with single newline
    doc = doc.rstrip() + "\n"

    return doc


def generate_filename(project_name: str, version: str) -> str:
    """Generate the canonical filename for the final document.

    Format: {ProjectName}_Build_Guide_{version}.md

    Args:
        project_name: The project name.
        version: Version string (e.g., 'v1').

    Returns:
        The filename string.
    """
    # Clean project name for filename
    safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", project_name).strip("_")
    return f"{safe_name}_Build_Guide_{version}.md"


def add_version_header(document: str, project_name: str, version: str, date: str | None = None) -> str:
    """Add version metadata header to the document.

    Args:
        document: The document content.
        project_name: Project name.
        version: Version string.
        date: Optional date string. Defaults to current UTC date.

    Returns:
        Document with version header prepended.
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    header = (
        f"# {project_name} — Build Guide\n\n"
        f"**Version:** {version}  \n"
        f"**Date:** {date}  \n"
        f"**Status:** Final  \n\n"
        f"---\n\n"
    )

    return header + document


def export_markdown(document: str, project_slug: str, filename: str) -> str:
    """Write the final document to the output directory.

    Args:
        document: The complete document content.
        project_slug: The project slug for the output subdirectory.
        filename: The filename to write.

    Returns:
        The full output path as a string.
    """
    output_dir = OUTPUT_DIR / project_slug
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename

    output_path.write_text(document, encoding="utf-8")
    return str(output_path)


def assemble_full_document(
    chapter_paths: list[str],
    chapter_titles: list[str],
    project_name: str,
    project_slug: str,
    version: str,
) -> dict:
    """Full assembly pipeline: compile, format, header, export.

    Args:
        chapter_paths: Ordered list of chapter file paths.
        chapter_titles: Ordered list of chapter titles.
        project_name: The project name.
        project_slug: URL-safe project identifier.
        version: Version string (e.g., 'v1').

    Returns:
        Dict with 'filename', 'output_path', and 'content'.
    """
    # Step 1: Compile
    compiled = compile_document(chapter_paths, chapter_titles)

    # Step 2: Format
    formatted = apply_formatting(compiled)

    # Step 3: Add version header
    with_header = add_version_header(formatted, project_name, version)

    # Step 4: Generate filename
    filename = generate_filename(project_name, version)

    # Step 5: Export
    output_path = export_markdown(with_header, project_slug, filename)

    return {
        "filename": filename,
        "output_path": output_path,
        "content": with_header,
    }
