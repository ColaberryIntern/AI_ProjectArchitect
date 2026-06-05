"""Per-user CLAUDE.md + PROGRESS.md scaffold + 4-layer context assembler.

Implements Operator 1 (BC todo 9967247766). See docs/specs/operator-01-per-user-scaffold.md.

The assembler concatenates 4 layers of context in priority order. Higher-priority
layers win on conflict. Claude Code is told this explicitly via the layer banners.

    Layer 1 (highest): Colaberry org policy
       Source: https://raw.githubusercontent.com/ColaberryIntern/AI_ProjectArchitect/main/CLAUDE.md
       TTL:    1 hour
       Cache:  ~/.claude/colaberry/CLAUDE.md

    Layer 2: Colaberry shared knowledge base
       Sources: www.colaberry.com, www.colaberry.ai, www.enterprise.colaberry.com (scraped)
       TTL:     24 hours
       Cache:   ~/.claude/colaberry/knowledge/{slug}.md

    Layer 3: Tenant policy (optional)
       Source: .claude/tenant/CLAUDE.md (if the tenant has set one)

    Layer 4 (lowest): Per-user policy + learned memory
       Sources: <workspace>/CLAUDE.md and <workspace>/OPERATOR_MEMORY.md
       Both committed to the user's workspace repo; admin can read.

The module is intentionally stdlib-only for v01 so it works inside any
container or local Python install without new dependencies.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ----- Source registry (Ali-controlled, 2026-06-05) ------------------------

ORG_CLAUDE_MD_RAW_URL = (
    "https://raw.githubusercontent.com/ColaberryIntern/AI_ProjectArchitect/main/CLAUDE.md"
)

SHARED_KB_SOURCES = [
    {"slug": "colaberry-com",     "url": "https://www.colaberry.com"},
    {"slug": "colaberry-ai",      "url": "https://www.colaberry.ai"},
    {"slug": "enterprise",        "url": "https://www.enterprise.colaberry.com"},
]

ORG_TTL_SECONDS = 60 * 60          # 1 hour
KB_TTL_SECONDS = 24 * 60 * 60      # 24 hours

USER_AGENT = "Colaberry Operator Scaffold (ali@colaberry.com)"

# ----- Domain types --------------------------------------------------------

@dataclass
class AssembledLayer:
    """One layer of the assembled context."""
    name: str                       # e.g. "Colaberry org policy"
    priority_label: str             # e.g. "Layer 1 (highest priority)"
    source: str                     # human-readable source description
    body: str                       # the markdown text
    fetched_at: float               # epoch seconds; 0 if from cache fallback only
    ok: bool                        # True if fetch succeeded; False if degraded


@dataclass
class AssembledContext:
    """Output of assemble_context()."""
    user_email: str
    user_display_name: str
    tenant_id: Optional[str]
    layers: list[AssembledLayer] = field(default_factory=list)
    assembled_at: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def as_concatenated_markdown(self) -> str:
        """Render the full concatenated CLAUDE.md text Claude Code sees at session start."""
        out = []
        for layer in self.layers:
            banner = f"# === {layer.priority_label}: {layer.name} ===\n# Source: {layer.source}\n"
            if not layer.ok:
                banner += f"# WARNING: this layer is degraded. Reason: {layer.body[:120]!r}\n"
            out.append(banner)
            out.append(layer.body.rstrip())
            out.append("\n")
        return "\n".join(out)


# ----- Fetchers ------------------------------------------------------------

def _http_get(url: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Read-only HTTP GET. Returns (ok, body_or_error).

    Stdlib only. No retries in v01. Caller decides cache fallback.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return True, resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"URLError: {e.reason}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def fetch_org_claude_md(local_fallback_path: Optional[Path] = None) -> AssembledLayer:
    """Fetch the org CLAUDE.md from GitHub raw with optional local fallback.

    For v01 the GitHub raw URL may 404 because the kickoff specs haven't been
    pushed yet. When it does, fall back to local_fallback_path (typically the
    project root CLAUDE.md).
    """
    ok, body = _http_get(ORG_CLAUDE_MD_RAW_URL)
    if ok:
        return AssembledLayer(
            name="Colaberry org policy",
            priority_label="Layer 1 (highest priority)",
            source=ORG_CLAUDE_MD_RAW_URL,
            body=body,
            fetched_at=time.time(),
            ok=True,
        )
    # Degraded path: try local fallback
    if local_fallback_path and local_fallback_path.exists():
        return AssembledLayer(
            name="Colaberry org policy",
            priority_label="Layer 1 (highest priority)",
            source=f"local fallback: {local_fallback_path} (GitHub raw fetch failed: {body})",
            body=local_fallback_path.read_text(encoding="utf-8"),
            fetched_at=time.time(),
            ok=False,
        )
    # Hard failure: no remote, no local
    return AssembledLayer(
        name="Colaberry org policy",
        priority_label="Layer 1 (highest priority)",
        source=ORG_CLAUDE_MD_RAW_URL,
        body=f"FETCH FAILED: {body}. No local fallback available.",
        fetched_at=0.0,
        ok=False,
    )


# Minimal HTML-to-text extractor. Stdlib only; not perfect but works for
# marketing/landing pages. We strip <script>, <style>, <nav>, <footer> tags
# in full, then collapse all other tags to their text content.
_HTML_REMOVE_BLOCKS = re.compile(
    r"<(script|style|nav|footer|svg|noscript)\b[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_HTML_TAGS = re.compile(r"<[^>]+>")
_HTML_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_HTML_BLANK_LINES = re.compile(r"\n{3,}")


def _html_to_text(html: str, max_chars: int = 20000) -> str:
    """Crude HTML → plain text. Strips script/style/nav/footer, collapses tags + whitespace."""
    cleaned = _HTML_REMOVE_BLOCKS.sub(" ", html)
    text = _HTML_TAGS.sub(" ", cleaned)
    text = _HTML_WHITESPACE.sub(" ", text)
    text = _HTML_BLANK_LINES.sub("\n\n", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[truncated at " + str(max_chars) + " chars]"
    return text


def scrape_colaberry_knowledge() -> list[AssembledLayer]:
    """Scrape the 3 colaberry sites. Returns one AssembledLayer per site.

    Each layer's body is the extracted plain text. Empty sites or fetch failures
    surface as ok=False layers (still included so the operator sees the degradation).
    """
    out = []
    for src in SHARED_KB_SOURCES:
        ok, body = _http_get(src["url"])
        if ok:
            text = _html_to_text(body)
            layer_body = text or "[fetched OK but extracted text was empty]"
            out.append(AssembledLayer(
                name=f"Colaberry shared knowledge - {src['slug']}",
                priority_label="Layer 2",
                source=src["url"],
                body=layer_body,
                fetched_at=time.time(),
                ok=bool(text),
            ))
        else:
            out.append(AssembledLayer(
                name=f"Colaberry shared knowledge - {src['slug']}",
                priority_label="Layer 2",
                source=src["url"],
                body=f"FETCH FAILED: {body}",
                fetched_at=0.0,
                ok=False,
            ))
    return out


def read_tenant_claude_md(workspace_dir: Path) -> Optional[AssembledLayer]:
    """If <workspace>/.claude/tenant/CLAUDE.md exists, read and wrap it. Otherwise None."""
    p = workspace_dir / ".claude" / "tenant" / "CLAUDE.md"
    if not p.exists():
        return None
    return AssembledLayer(
        name="Tenant policy",
        priority_label="Layer 3",
        source=str(p),
        body=p.read_text(encoding="utf-8"),
        fetched_at=time.time(),
        ok=True,
    )


def read_user_claude_md(workspace_dir: Path) -> AssembledLayer:
    """Read <workspace>/CLAUDE.md (per-user). If missing, return a placeholder layer."""
    p = workspace_dir / "CLAUDE.md"
    if not p.exists():
        return AssembledLayer(
            name="Per-user policy",
            priority_label="Layer 4 (lowest priority)",
            source=str(p),
            body="[Per-user CLAUDE.md not yet provisioned. Run seed_workspace() first.]",
            fetched_at=0.0,
            ok=False,
        )
    return AssembledLayer(
        name="Per-user policy",
        priority_label="Layer 4 (lowest priority)",
        source=str(p),
        body=p.read_text(encoding="utf-8"),
        fetched_at=time.time(),
        ok=True,
    )


def read_operator_memory(workspace_dir: Path) -> Optional[AssembledLayer]:
    """If <workspace>/OPERATOR_MEMORY.md exists, read and wrap it as the lowest layer.

    Operator 5 owns the contents; Operator 1 just reads it if present.
    """
    p = workspace_dir / "OPERATOR_MEMORY.md"
    if not p.exists():
        return None
    return AssembledLayer(
        name="Operator memory (learned)",
        priority_label="Layer 5 (lowest priority - never overrides anything above)",
        source=str(p),
        body=p.read_text(encoding="utf-8"),
        fetched_at=time.time(),
        ok=True,
    )


# ----- Assembly ------------------------------------------------------------

def assemble_context(
    user_email: str,
    user_display_name: str,
    workspace_dir: Path,
    tenant_id: Optional[str] = None,
    org_local_fallback: Optional[Path] = None,
) -> AssembledContext:
    """Assemble the 4 (or 5) layers into a single context payload for Claude Code.

    Order: org -> shared KB -> tenant (if any) -> per-user -> operator memory (if any).
    """
    ctx = AssembledContext(
        user_email=user_email,
        user_display_name=user_display_name,
        tenant_id=tenant_id,
        assembled_at=time.time(),
    )

    # Layer 1: org
    layer_org = fetch_org_claude_md(local_fallback_path=org_local_fallback)
    ctx.layers.append(layer_org)
    if not layer_org.ok:
        ctx.warnings.append(f"Org CLAUDE.md degraded: {layer_org.source}")

    # Layer 2: shared KB (3 sites)
    for kb_layer in scrape_colaberry_knowledge():
        ctx.layers.append(kb_layer)
        if not kb_layer.ok:
            ctx.warnings.append(f"KB source degraded: {kb_layer.source}")

    # Layer 3: tenant (optional)
    tenant_layer = read_tenant_claude_md(workspace_dir)
    if tenant_layer:
        ctx.layers.append(tenant_layer)

    # Layer 4: per-user
    user_layer = read_user_claude_md(workspace_dir)
    ctx.layers.append(user_layer)
    if not user_layer.ok:
        ctx.warnings.append(f"Per-user CLAUDE.md not provisioned at {user_layer.source}")

    # Layer 5: operator memory (optional)
    mem_layer = read_operator_memory(workspace_dir)
    if mem_layer:
        ctx.layers.append(mem_layer)

    return ctx


# ----- Starter templates ---------------------------------------------------

def render_starter_claude_md(user_email: str, user_display_name: str, tenant_id: Optional[str] = None) -> str:
    """Produce the starter per-user CLAUDE.md text seeded at workspace creation."""
    tenant_line = f"Your tenant: **{tenant_id}**" if tenant_id else "Your tenant: none assigned"
    return f"""# CLAUDE.md — personal preferences for {user_display_name}

Your email: **{user_email}**
{tenant_line}

> This is your personal CLAUDE.md. **Colaberry-wide rules in `~/.claude/colaberry/CLAUDE.md`
> always override anything in this file.** The shared knowledge base in
> `~/.claude/colaberry/knowledge/` is also read by Claude Code at session start and
> wins over your preferences on conflict.
>
> What goes in this file:
> - Your personal coding preferences (e.g. "I prefer Black over autopep8")
> - Your communication style (e.g. "Keep summaries to 3 bullets max")
> - Personal context Claude Code should remember (your role, current focus, etc.)
>
> What does NOT go in this file:
> - Company-wide policy (that lives in the org CLAUDE.md — edit there if you're an admin)
> - Things you've corrected Claude on (those auto-flow into OPERATOR_MEMORY.md)

---

## Your role

(Auto-populated at provisioning from the tools-access matrix. Edit if your role changes.)

- Display name: {user_display_name}
- Email: {user_email}
- Tenant: {tenant_id or 'unassigned'}

## Your preferences

(Write your preferences here. Examples:)

- Default code style:
- Default communication style:
- Recurring asks (things you do every session):

## Your scope of access

(Auto-populated from the tools-access provisioning matrix [Admin 2].
Re-generated by Claude Code on the next session start if it drifts.)

- (will be filled in by the session-start hook)

---

_Seeded by Operator 1 scaffold on {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}._
"""


def render_starter_progress_md(user_email: str, user_display_name: str) -> str:
    """Produce the starter per-user PROGRESS.md text seeded at workspace creation."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    return f"""# PROGRESS.md — work log for {user_display_name}

Your email: **{user_email}**
Workspace created: {today}

This file tracks your work over time. Claude Code reads this at session start to know
what you've been working on. Each completed unit of work lands here as an entry.

---

## Current Focus

(Write your current focus here. Claude Code uses this as context.)

---

## Completed Work

(Entries land here as you complete BC tickets. Format follows the org CLAUDE.md
PROGRESS.md doctrine — one entry per completed change, with verification evidence.)

---

## Upcoming Work

(Things you know are coming but haven't started.)

---

_Seeded by Operator 1 scaffold on {today}._
"""


# ----- Workspace seeding ---------------------------------------------------

def seed_workspace(
    workspace_dir: Path,
    user_email: str,
    user_display_name: str,
    tenant_id: Optional[str] = None,
    overwrite: bool = False,
) -> dict:
    """Write CLAUDE.md + PROGRESS.md + .claude/colaberry/ scaffolding into workspace_dir.

    Idempotent: skips existing files unless overwrite=True. Returns a manifest of
    files written / skipped for audit.

    File layout produced:
        <workspace>/
        ├── CLAUDE.md                          ← per-user (Layer 4)
        ├── PROGRESS.md                        ← per-user work log
        ├── .claude/
        │   ├── colaberry/                     ← auto-fetched at session start (Layer 1+2)
        │   │   └── .gitkeep
        │   ├── tenant/                        ← optional tenant policy (Layer 3)
        │   │   └── .gitkeep
        │   └── README.md                      ← explains the layout
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"workspace": str(workspace_dir), "written": [], "skipped": []}

    files_to_write = {
        "CLAUDE.md": render_starter_claude_md(user_email, user_display_name, tenant_id),
        "PROGRESS.md": render_starter_progress_md(user_email, user_display_name),
        ".claude/colaberry/.gitkeep": "",
        ".claude/tenant/.gitkeep": "",
        ".claude/README.md": (
            "# .claude/ — Colaberry-managed scaffolding\n\n"
            "- `colaberry/CLAUDE.md` (1h TTL) — the org doctrine, fetched from GitHub raw.\n"
            "- `colaberry/knowledge/*.md` (24h TTL) — scraped from the 3 colaberry.com sites.\n"
            "- `tenant/CLAUDE.md` — optional tenant-specific policy.\n\n"
            "Do not hand-edit files under `colaberry/` — they are auto-refreshed.\n"
            "Edit `tenant/CLAUDE.md` if you are a tenant admin.\n"
            "Edit `../CLAUDE.md` (the per-user file in the workspace root) for your own preferences.\n"
        ),
    }

    for relpath, content in files_to_write.items():
        target = workspace_dir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            manifest["skipped"].append(str(target))
            continue
        target.write_text(content, encoding="utf-8")
        manifest["written"].append(str(target))

    return manifest
