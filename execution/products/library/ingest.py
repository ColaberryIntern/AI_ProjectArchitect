"""Ingest orchestrator — drives the fetch → parse → classify → enrich →
submit pipeline. Supports both single-URL and GitHub-repo (batch) modes.

Batches run on a thread-pool: one task per file/URL. Per-item progress
written to output/library/_ingestion/<batch_id>/items.jsonl so the
progress page can poll it.

Per-batch lifecycle:
    1. create_batch(...) → returns batch_id, writes batch header
    2. enqueue_items(batch_id, [...])   for each URL/file
    3. worker thread drains the queue, processes each item, writes result
    4. UI polls batch_status(batch_id) to render live progress
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import fetcher, parser as parser_mod, store
from .enricher import enrich
from .trusted import should_auto_vet

LAYER = "product"
PRODUCT = "library"

ROOT = Path(__file__).resolve().parents[3]
INGEST_ROOT = ROOT / "output" / "library" / "_ingestion"
INGEST_ROOT.mkdir(parents=True, exist_ok=True)

MAX_BATCH_ITEMS = 200
MAX_PARALLEL = 4


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── Data classes ──────────────────────────────────────────────────────


@dataclass
class IngestItem:
    """One unit of ingestion — a single URL or repo-relative file path."""

    item_id: str
    source_url: str
    repo: str | None = None
    path: str | None = None
    ref: str = "main"
    category_hint: str | None = None


@dataclass
class IngestResult:
    item_id: str
    source_url: str
    status: str = "queued"          # queued | fetching | parsing | classifying | submitted | failed | duplicate
    category: str | None = None
    confidence: float | None = None
    asset_name: str | None = None
    submission_id: str | None = None
    auto_vetted: bool = False
    auto_vet_reason: str | None = None
    quality_score: float = 0.0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    finished_at: str | None = None


@dataclass
class BatchHeader:
    batch_id: str
    workspace: str
    submitter: str
    source: str            # human-readable: "URL: ..." or "GitHub: owner/repo"
    created_at: str
    total_items: int = 0
    status: str = "running"  # running | done | failed
    finished_at: str | None = None


# ── Batch I/O ─────────────────────────────────────────────────────────


def _batch_dir(batch_id: str) -> Path:
    p = INGEST_ROOT / batch_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _header_path(batch_id: str) -> Path:
    return _batch_dir(batch_id) / "header.json"


def _items_path(batch_id: str) -> Path:
    return _batch_dir(batch_id) / "items.jsonl"


def _write_header(h: BatchHeader) -> None:
    _header_path(h.batch_id).write_text(json.dumps(asdict(h), indent=2), encoding="utf-8")


def _append_result(batch_id: str, r: IngestResult) -> None:
    with _items_path(batch_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(r)) + "\n")


def create_batch(workspace: str, submitter: str, source: str) -> BatchHeader:
    """Start a new batch. Returns the header (which has the batch_id)."""
    h = BatchHeader(
        batch_id=str(uuid.uuid4())[:12],
        workspace=workspace,
        submitter=submitter,
        source=source,
        created_at=_now(),
    )
    _write_header(h)
    return h


def batch_header(batch_id: str) -> BatchHeader | None:
    p = _header_path(batch_id)
    if not p.exists():
        return None
    try:
        return BatchHeader(**json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return None


def batch_results(batch_id: str) -> list[IngestResult]:
    p = _items_path(batch_id)
    if not p.exists():
        return []
    out: list[IngestResult] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(IngestResult(**json.loads(line)))
        except Exception:
            pass
    return out


def list_recent_batches(limit: int = 20) -> list[BatchHeader]:
    if not INGEST_ROOT.exists():
        return []
    out: list[BatchHeader] = []
    for d in sorted(INGEST_ROOT.iterdir(), key=lambda x: x.stat().st_mtime,
                          reverse=True):
        if not d.is_dir():
            continue
        h = batch_header(d.name)
        if h:
            out.append(h)
        if len(out) >= limit:
            break
    return out


# ── Per-item processor ────────────────────────────────────────────────


def _process_one(workspace: str, submitter: str, batch_id: str,
                  item: IngestItem) -> IngestResult:
    """Run one item through the full pipeline. Never raises."""
    r = IngestResult(item_id=item.item_id, source_url=item.source_url,
                            status="fetching")
    # Note: we don't append the initial "fetching" record — only the final
    # outcome — so each item produces exactly one row in items.jsonl.

    try:
        # 1 — Fetch
        if item.repo and item.path:
            owner, repo = item.repo.split("/", 1)
            fetched = fetcher.fetch_github_file(owner, repo, item.path,
                                                            ref=item.ref or "main")
        else:
            fetched = fetcher.fetch_url(item.source_url)
        if not fetched.ok or not fetched.document:
            r.status = "failed"
            r.error = fetched.error or "fetch returned no document"
            r.finished_at = _now()
            _append_result(batch_id, r)
            return r
        doc = fetched.document

        # 2 — Parse
        r.status = "parsing"
        parsed = parser_mod.parse(doc.content, doc.content_type, doc.path)

        # 3 — Classify + enrich (done together in enrich)
        r.status = "classifying"
        enriched = enrich(
            parsed=parsed,
            raw_content=doc.content,
            source_url=item.source_url,
            category_hint=item.category_hint,
        )

        # 4 — Decide trusted auto-vet
        auto_vet, vet_reason = should_auto_vet(
            item.source_url, enriched.classification.get("confidence", 0.0),
        )

        # 5 — Submit
        sub = store.submit(
            workspace=workspace,
            category=enriched.category,
            submitted_by=submitter,
            name=enriched.name,
            description=enriched.description,
            how_to_use=enriched.how_to_use,
            example=enriched.example,
            tags=enriched.tags,
            source=item.source_url,
        )

        if auto_vet:
            reviewed = store.review_submission(
                workspace=workspace, submission_id=sub.submission_id,
                decision="accepted", reviewer=f"trusted-source:{submitter}",
                notes=f"Auto-vetted: {vet_reason}",
            )
            r.auto_vetted = True
            r.auto_vet_reason = vet_reason

        r.status = "submitted"
        r.category = enriched.category
        r.confidence = enriched.classification.get("confidence")
        r.asset_name = enriched.name
        r.submission_id = sub.submission_id
        r.quality_score = enriched.quality_score
        r.warnings = list(enriched.warnings)

    except Exception as e:
        r.status = "failed"
        r.error = f"{type(e).__name__}: {e}"

    r.finished_at = _now()
    _append_result(batch_id, r)
    return r


def reset_for_tests() -> None:
    """Reset module-level state (used by isolated test fixtures)."""
    _BATCH_FUTURES.clear()


# ── Bulk enrichment driver (reuses the batch infrastructure) ────────


def _run_enrichment_batch(workspace: str, enricher_id: str, batch_id: str,
                                  items: list[dict], force: bool) -> None:
    from .enrichment_job import enrich_asset

    def _enrich_one(item):
        category = item.get("category") or "skills"
        asset_id = item.get("asset_id") or item.get("name") or ""
        source_url = item.get("source_url") or item.get("source") or ""
        r = IngestResult(item_id=item.get("item_id", ""),
                                source_url=source_url, status="enriching")
        try:
            meta = enrich_asset(workspace, category, asset_id, source_url,
                                      enricher_id, force=force)
            if meta.enrichment_state == "enriched":
                r.status = "submitted"
                r.category = category
                r.asset_name = asset_id
                r.quality_score = 1.0 if meta.readme_markdown else 0.5
            elif meta.enrichment_state == "failed":
                r.status = "failed"
                r.error = meta.enrichment_error or "unknown"
            else:
                r.status = "skipped"
        except Exception as e:
            r.status = "failed"
            r.error = f"{type(e).__name__}: {e}"
        r.finished_at = _now()
        _append_result(batch_id, r)
        return r

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        list(pool.map(_enrich_one, items))

    h = batch_header(batch_id)
    if h:
        h.status = "done"
        h.finished_at = _now()
        _write_header(h)


def enrich_category(workspace: str, enricher_id: str, category: str,
                          force: bool = False) -> BatchHeader:
    """Bulk-enrich every asset in `category` for `workspace`."""
    from . import inventory

    raw_items = inventory.load_category(category) or []
    items_to_enrich = []
    for i, raw in enumerate(raw_items):
        items_to_enrich.append({
            "item_id": f"{i:04d}",
            "category": category,
            "asset_id": raw.get("name") or raw.get("id"),
            "source_url": raw.get("source") or raw.get("source_url"),
        })

    h = create_batch(workspace, enricher_id,
                            f"Enrich: {category} ({'force' if force else 'incremental'})")
    h.total_items = len(items_to_enrich)
    _write_header(h)

    if not items_to_enrich:
        h.status = "done"
        h.finished_at = _now()
        _write_header(h)
        return h

    t = threading.Thread(
        target=_run_enrichment_batch,
        args=(workspace, enricher_id, h.batch_id, items_to_enrich, force),
        daemon=True,
    )
    _BATCH_FUTURES[h.batch_id] = t
    t.start()
    return h


# ── Batch drivers ─────────────────────────────────────────────────────


_BATCH_FUTURES: dict[str, threading.Thread] = {}


def _run_batch_thread(workspace: str, submitter: str, batch_id: str,
                            items: list[IngestItem]) -> None:
    """Process all items, then close the batch."""
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        list(pool.map(
            lambda it: _process_one(workspace, submitter, batch_id, it),
            items,
        ))
    # Close
    h = batch_header(batch_id)
    if h:
        h.status = "done"
        h.finished_at = _now()
        _write_header(h)


def ingest_url(workspace: str, submitter: str, url: str) -> BatchHeader:
    """Single-URL ingest. Creates a one-item batch and runs it in a worker thread."""
    h = create_batch(workspace, submitter, f"URL: {url}")
    h.total_items = 1
    _write_header(h)
    item = IngestItem(item_id="0001", source_url=url)
    t = threading.Thread(
        target=_run_batch_thread, args=(workspace, submitter, h.batch_id, [item]),
        daemon=True,
    )
    _BATCH_FUTURES[h.batch_id] = t
    t.start()
    return h


def ingest_github_repo(workspace: str, submitter: str, github_url: str,
                              ref: str = "main") -> BatchHeader:
    """Repo-wide batch ingest. Walks the tree, filters interesting files,
    starts a worker thread that processes them in parallel."""
    parsed = fetcher.parse_github_url(github_url)
    if not parsed:
        h = create_batch(workspace, submitter, f"GitHub: {github_url} (invalid URL)")
        h.status = "failed"
        h.finished_at = _now()
        _write_header(h)
        return h

    owner = parsed["owner"] or ""
    repo = parsed["repo"] or ""
    # Resolve the real default branch — handles repos using master instead of main.
    ref_used = fetcher.resolve_default_branch(owner, repo, parsed["ref"] or ref)
    h = create_batch(workspace, submitter, f"GitHub: {owner}/{repo}@{ref_used}")

    tree = fetcher.fetch_github_tree(owner, repo, ref_used)
    interesting = fetcher.filter_interesting_files(tree, max_files=MAX_BATCH_ITEMS)

    items = [
        IngestItem(
            item_id=f"{i:04d}",
            source_url=f"https://github.com/{owner}/{repo}/blob/{ref_used}/{entry['path']}",
            repo=f"{owner}/{repo}",
            path=entry["path"],
            ref=ref_used,
            category_hint=entry["_category_hint"],
        )
        for i, entry in enumerate(interesting)
    ]

    h.total_items = len(items)
    _write_header(h)

    if not items:
        h.status = "done"
        h.finished_at = _now()
        _write_header(h)
        return h

    t = threading.Thread(
        target=_run_batch_thread, args=(workspace, submitter, h.batch_id, items),
        daemon=True,
    )
    _BATCH_FUTURES[h.batch_id] = t
    t.start()
    return h


# ── Status helpers (for the UI) ───────────────────────────────────────


def batch_status(batch_id: str) -> dict[str, Any]:
    h = batch_header(batch_id)
    if not h:
        return {"error": "not found"}
    results = batch_results(batch_id)
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    return {
        "header": asdict(h),
        "by_status": by_status,
        "processed": len(results),
        "total": h.total_items,
        # Renamed from "items" → "recent" to avoid Jinja's dict.items() collision.
        "recent": [asdict(r) for r in results[-50:]],
        "all_results": [asdict(r) for r in results],
    }
