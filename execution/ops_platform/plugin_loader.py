"""Plugin loader — scans the /plugins tree, validates manifests, returns canonical Capability dicts.

The loader is the **only** code path that reads /plugins from disk. Every other
ops_platform module receives validated capability dicts from the loader (or
from the capability_registry which caches them).

Design rules:
- A "plugin" is a folder under /plugins/<type>/<slug>/ containing manifest.json.
- The manifest is validated against config/schemas/ops/plugin_manifest.schema.json.
- A plugin's ``id`` field is canonical. The folder slug is not used as ID — that
  lets teams rename folders without breaking traces.
- Plugins with invalid manifests are SKIPPED and the failure is recorded; the
  loader does not raise on a single bad plugin (that would let one broken plugin
  brick the whole platform).
- Loading is idempotent and cheap; the registry can call it on every request
  during dev and once at startup in prod.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

from config.settings import PROJECT_ROOT, SCHEMAS_DIR

logger = logging.getLogger(__name__)

# Where the loader looks for plugins. Each subfolder is a plugin type.
PLUGIN_ROOT = PROJECT_ROOT / "plugins"

PLUGIN_TYPES = ("workflows", "agents", "mcp-servers", "skills", "prompt-packs")

MANIFEST_FILENAME = "manifest.json"

_MANIFEST_SCHEMA_PATH = SCHEMAS_DIR / "ops" / "plugin_manifest.schema.json"


@dataclass
class LoadResult:
    """Result of a load pass. Carries everything the registry needs to know."""

    capabilities: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def by_id(self) -> dict[str, dict]:
        return {c["id"]: c for c in self.capabilities}


def _load_schema() -> dict:
    """Read the manifest schema once. Cached at module level after first call."""
    global _SCHEMA_CACHE
    try:
        return _SCHEMA_CACHE
    except NameError:
        with open(_MANIFEST_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE = json.load(f)
        return _SCHEMA_CACHE


def _validate_manifest(manifest: dict, manifest_path: Path) -> list[str]:
    """Return a list of validation error messages. Empty list = valid."""
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda e: e.absolute_path)
    if not errors:
        return []
    return [
        f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in errors
    ]


def _safe_rel_or_absolute(path: Path) -> str:
    """Return path relative to PROJECT_ROOT when possible; otherwise the
    absolute path. Tests use tmp_path which lives outside PROJECT_ROOT —
    storing the absolute path lets the workflow_runner still find the
    prompt file."""
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _enrich_manifest(manifest: dict, plugin_dir: Path, plugin_type: str) -> dict:
    """Augment the manifest with runtime metadata the loader knows but the
    plugin author shouldn't have to repeat: source paths, derived counts,
    relative asset URLs.
    """
    enriched = dict(manifest)
    enriched["_meta"] = {
        "plugin_dir": _safe_rel_or_absolute(plugin_dir),
        "plugin_dir_absolute": str(plugin_dir),
        "plugin_type_folder": plugin_type,
        "manifest_path": _safe_rel_or_absolute(plugin_dir / MANIFEST_FILENAME),
        "has_readme": (plugin_dir / (manifest.get("readme_path") or "README.md")).exists(),
        "has_prompt": _has_optional_file(plugin_dir, manifest.get("prompt_path")),
        "has_workflow_yaml": _has_optional_file(plugin_dir, manifest.get("workflow_yaml_path")),
        "training_walkthrough_exists": _has_optional_file(
            plugin_dir,
            (manifest.get("training_video") or {}).get("generated_walkthrough_path"),
        ),
    }
    return enriched


def _has_optional_file(plugin_dir: Path, relative: str | None) -> bool:
    if not relative:
        return False
    return (plugin_dir / relative).exists()


def _scan_plugin_type(type_folder: Path, plugin_type: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Scan one /plugins/<type>/ folder. Returns (capabilities, errors, skipped)."""
    capabilities: list[dict] = []
    errors: list[dict] = []
    skipped: list[dict] = []

    if not type_folder.exists():
        return capabilities, errors, skipped

    for plugin_dir in sorted(type_folder.iterdir()):
        if not plugin_dir.is_dir() or plugin_dir.name.startswith("."):
            continue
        manifest_path = plugin_dir / MANIFEST_FILENAME
        if not manifest_path.exists():
            skipped.append({"path": str(plugin_dir), "reason": "no manifest.json"})
            continue

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            errors.append({"path": str(manifest_path), "error": f"unreadable: {e}"})
            continue

        validation_errors = _validate_manifest(manifest, manifest_path)
        if validation_errors:
            errors.append({
                "path": str(manifest_path),
                "error": "manifest validation failed",
                "details": validation_errors,
            })
            continue

        if manifest.get("type") != _type_from_folder(plugin_type):
            errors.append({
                "path": str(manifest_path),
                "error": (
                    f"manifest type='{manifest.get('type')}' does not match folder "
                    f"'{plugin_type}' (expected '{_type_from_folder(plugin_type)}')"
                ),
            })
            continue

        capabilities.append(_enrich_manifest(manifest, plugin_dir, plugin_type))

    return capabilities, errors, skipped


def _type_from_folder(folder_name: str) -> str:
    """Map folder name ('mcp-servers') -> manifest type field ('mcp_server')."""
    return {
        "workflows": "workflow",
        "agents": "agent",
        "mcp-servers": "mcp_server",
        "skills": "skill",
        "prompt-packs": "prompt_pack",
    }.get(folder_name, folder_name)


def load_plugins(root: Path | None = None) -> LoadResult:
    """Scan the entire /plugins tree and return a LoadResult.

    Args:
        root: Override plugin root (used by tests). Defaults to PROJECT_ROOT/plugins.

    Returns:
        LoadResult with capabilities, errors, skipped. Never raises.
    """
    plugin_root = root or PLUGIN_ROOT
    result = LoadResult()

    if not plugin_root.exists():
        logger.info("Plugin root %s does not exist; returning empty registry", plugin_root)
        return result

    seen_ids: set[str] = set()

    for plugin_type in PLUGIN_TYPES:
        caps, errs, skipped = _scan_plugin_type(plugin_root / plugin_type, plugin_type)
        for cap in caps:
            cid = cap["id"]
            if cid in seen_ids:
                result.errors.append({
                    "path": cap["_meta"]["manifest_path"],
                    "error": f"duplicate id '{cid}' — already registered by another plugin",
                })
                continue
            seen_ids.add(cid)
            result.capabilities.append(cap)
        result.errors.extend(errs)
        result.skipped.extend(skipped)

    if result.errors:
        logger.warning(
            "Plugin loader finished with %d errors and %d capabilities",
            len(result.errors), len(result.capabilities),
        )
    else:
        logger.info("Plugin loader loaded %d capabilities cleanly", len(result.capabilities))

    return result
