"""Internal marketplace — fork-and-publish for proven capabilities and
pipelines within the org.

This is NOT a public sharing surface. Templates are visible org-wide but
forking always lands in a workspace; the lineage chain remembers
(parent_template_id, forked_from, derived_versions[]) for reproducibility.

Persistence
-----------
``output/ops_platform/marketplace/{template_id}.json``
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import (
    audit_log, capability_versions, pipeline_engine, workspaces,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

_MARKET_DIR = OUTPUT_DIR / "ops_platform" / "marketplace"
_FORKED_DIR = OUTPUT_DIR / "ops_platform" / "forked_capabilities"


@dataclass
class Template:
    template_id: str
    title: str
    template_kind: str       # "capability" | "pipeline"
    source_id: str           # capability_id or pipeline_id
    source_version_id: str | None
    manifest_snapshot: dict
    prompt_snapshot: str | None
    category: str
    tags: list = field(default_factory=list)
    trust_badges: list = field(default_factory=list)
    estimated_setup_minutes: int = 5
    compatibility_notes: str = ""
    published_by: dict = field(default_factory=dict)
    published_at: str = ""
    parent_template_id: str | None = None
    forked_from: str | None = None
    derived_versions: list = field(default_factory=list)
    fork_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def publish_capability_template(
    *,
    capability_id: str,
    title: str,
    category: str,
    tags: list | None = None,
    trust_badges: list | None = None,
    estimated_setup_minutes: int = 5,
    compatibility_notes: str = "",
    published_by: dict | str = "anonymous",
    version_id: str | None = None,
    registry: CapabilityRegistry | None = None,
) -> Template:
    reg = registry or default_registry()
    cap = reg.get(capability_id)
    if cap is None:
        raise ValueError(f"capability '{capability_id}' not registered")
    manifest_snapshot = {k: v for k, v in cap.items() if k != "_meta"}
    prompt_snapshot = None
    if version_id:
        v = capability_versions.get_version(version_id)
        if v:
            manifest_snapshot = v.manifest_snapshot
            prompt_snapshot = v.prompt_snapshot
    tpl = Template(
        template_id=f"tpl_{uuid.uuid4().hex[:12]}",
        title=title, template_kind="capability",
        source_id=capability_id, source_version_id=version_id,
        manifest_snapshot=manifest_snapshot,
        prompt_snapshot=prompt_snapshot,
        category=category, tags=list(tags or []),
        trust_badges=list(trust_badges or []),
        estimated_setup_minutes=estimated_setup_minutes,
        compatibility_notes=compatibility_notes,
        published_by=_normalize_actor(published_by),
        published_at=datetime.now(timezone.utc).isoformat(),
    )
    _persist(tpl)
    audit_log.record(
        action="marketplace.published", entity_type="template",
        entity_id=tpl.template_id, actor=tpl.published_by,
        new_state={"title": title, "kind": "capability", "source_id": capability_id},
    )
    return tpl


def publish_pipeline_template(
    *,
    pipeline_id: str,
    title: str,
    category: str,
    tags: list | None = None,
    trust_badges: list | None = None,
    estimated_setup_minutes: int = 10,
    compatibility_notes: str = "",
    published_by: dict | str = "anonymous",
) -> Template:
    manifest = pipeline_engine.load_pipeline(pipeline_id)
    if manifest is None:
        raise ValueError(f"pipeline '{pipeline_id}' not found")
    tpl = Template(
        template_id=f"tpl_{uuid.uuid4().hex[:12]}",
        title=title, template_kind="pipeline",
        source_id=pipeline_id, source_version_id=None,
        manifest_snapshot=manifest, prompt_snapshot=None,
        category=category, tags=list(tags or []),
        trust_badges=list(trust_badges or []),
        estimated_setup_minutes=estimated_setup_minutes,
        compatibility_notes=compatibility_notes,
        published_by=_normalize_actor(published_by),
        published_at=datetime.now(timezone.utc).isoformat(),
    )
    _persist(tpl)
    audit_log.record(
        action="marketplace.published", entity_type="template",
        entity_id=tpl.template_id, actor=tpl.published_by,
        new_state={"title": title, "kind": "pipeline", "source_id": pipeline_id},
    )
    return tpl


def list_templates(*, category: str | None = None,
                    template_kind: str | None = None) -> list[Template]:
    if not _MARKET_DIR.exists():
        return []
    out: list[Template] = []
    for p in _MARKET_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(Template(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    if category:
        out = [t for t in out if t.category == category]
    if template_kind:
        out = [t for t in out if t.template_kind == template_kind]
    out.sort(key=lambda t: t.published_at, reverse=True)
    return out


def get_template(template_id: str) -> Template | None:
    path = _MARKET_DIR / f"{template_id}.json"
    if not path.exists():
        return None
    try:
        return Template(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def fork(
    template_id: str,
    *,
    workspace_id: str | None = None,
    actor: dict | str = "anonymous",
    new_id_prefix: str = "fork",
    correlation_id: str | None = None,
) -> dict:
    """Materialize a template inside a target workspace.

    For capability templates: writes a new manifest under
    ``output/ops_platform/forked_capabilities/{new_id}.json`` (the runtime
    plugin loader sees the live registry; this lineage record exists for
    audit + future hot-reload).

    For pipeline templates: persists via ``pipeline_engine.save_pipeline``
    so it shows up in `/ops/pipelines` immediately.

    Returns {forked_id, template_id, workspace_id, kind, error?}.
    """
    tpl = get_template(template_id)
    if tpl is None:
        return {"error": "template not found"}
    actor_norm = _normalize_actor(actor)
    cid = correlation_id or str(uuid.uuid4())

    if tpl.template_kind == "pipeline":
        manifest = dict(tpl.manifest_snapshot)
        new_pid = _make_unique_pipeline_id(manifest.get("pipeline_id", "forked"))
        manifest["pipeline_id"] = new_pid
        manifest["name"] = f"[fork] {manifest.get('name', new_pid)}"
        try:
            pipeline_engine.save_pipeline(manifest)
        except (ValueError, OSError) as e:
            return {"error": str(e)}
        if workspace_id:
            try:
                workspaces.attach_pipeline(workspace_id, new_pid, actor=actor_norm)
            except Exception:
                pass
        tpl.derived_versions.append(new_pid)
        tpl.fork_count = (tpl.fork_count or 0) + 1
        _persist(tpl)
        audit_log.record(
            action="marketplace.forked", entity_type="template",
            entity_id=tpl.template_id, actor=actor_norm,
            new_state={"forked_pipeline_id": new_pid, "workspace_id": workspace_id},
            correlation_id=cid,
        )
        return {"forked_id": new_pid, "template_id": template_id,
                "workspace_id": workspace_id, "kind": "pipeline"}

    # Capability template — write lineage record + (optionally) attach to workspace
    _FORKED_DIR.mkdir(parents=True, exist_ok=True)
    forked_dir = _FORKED_DIR
    new_cap_id = f"{new_id_prefix}_{tpl.source_id}_{uuid.uuid4().hex[:8]}"
    lineage = {
        "id": new_cap_id,
        "forked_from_template_id": tpl.template_id,
        "parent_capability_id": tpl.source_id,
        "manifest": dict(tpl.manifest_snapshot, id=new_cap_id),
        "prompt": tpl.prompt_snapshot or "",
        "forked_at": datetime.now(timezone.utc).isoformat(),
        "actor": actor_norm,
        "workspace_id": workspace_id,
    }
    (forked_dir / f"{new_cap_id}.json").write_text(
        json.dumps(lineage, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    if workspace_id:
        try:
            workspaces.attach_capability(workspace_id, new_cap_id, actor=actor_norm)
        except Exception:
            pass
    tpl.derived_versions.append(new_cap_id)
    tpl.fork_count = (tpl.fork_count or 0) + 1
    _persist(tpl)
    audit_log.record(
        action="marketplace.forked", entity_type="template",
        entity_id=tpl.template_id, actor=actor_norm,
        new_state={"forked_capability_id": new_cap_id, "workspace_id": workspace_id},
        correlation_id=cid,
    )
    return {"forked_id": new_cap_id, "template_id": template_id,
            "workspace_id": workspace_id, "kind": "capability"}


# ── Internal ───────────────────────────────────────────────────────────


def _normalize_actor(actor) -> dict:
    if isinstance(actor, dict):
        out = dict(actor)
        out.setdefault("name", "anonymous")
        return out
    return {"name": str(actor)}


def _persist(tpl: Template) -> None:
    _MARKET_DIR.mkdir(parents=True, exist_ok=True)
    (_MARKET_DIR / f"{tpl.template_id}.json").write_text(
        json.dumps(tpl.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8",
    )


_PIPELINE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")


def _make_unique_pipeline_id(base: str) -> str:
    candidate = f"{base[:40]}-fork-{uuid.uuid4().hex[:6]}".lower()
    candidate = re.sub(r"[^a-z0-9_-]+", "-", candidate).strip("-")
    if not _PIPELINE_ID_RE.match(candidate):
        candidate = f"forked-{uuid.uuid4().hex[:8]}"
    return candidate
