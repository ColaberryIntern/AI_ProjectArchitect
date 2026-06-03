"""Library product router — top-level /library/ shell.

Provides:
    - Overview with featured-of-the-day + scanner candidate count
    - Per-category listings (with vetted filter)
    - Per-asset detail page (description, how-to-use, example, ratings, comments)
    - Rating form (POST)
    - Comment form (POST)
    - Submission flow (Add to Library)
    - Pending review queue + accept/reject actions
    - Workspace picker (multi-tenancy)
    - Scanner candidates view

Multi-tenancy is workspace-scoped via the `?ws=<workspace>` query param
(falls back to "global"). Per-workspace data lives at output/library/<ws>/.
"""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from execution.products.library import (
    auth_google, enrichment_job, featured, ingest, inventory,
    scanner, search as search_mod, store, tenancy,
    use_case_generator, use_cases, word_cloud,
)

router = APIRouter(prefix="/library")


def _ws(request: Request) -> str:
    """Resolve workspace from query, header, or default."""
    ws = request.query_params.get("ws")
    if ws:
        return ws
    return request.headers.get("X-Workspace") or "global"


def _user(request: Request) -> str:
    """Resolve the actor (placeholder until auth is wired through Architect)."""
    return request.query_params.get("as") or request.headers.get("X-User") or "anonymous"


def _session_user(request: Request) -> "tenancy.User | None":
    """[Library 2] Resolve logged-in user from the SSO cookie."""
    cookie = request.cookies.get(auth_google.SESSION_COOKIE_NAME)
    return auth_google.current_user_from_cookie(cookie)


def _scope(request: Request, session_user) -> str:
    """[Library 2] Resolve effective scope: all | my-company | mine.

    Default: 'my-company' when logged in, 'all' when anonymous.
    URL param 'scope' overrides.
    """
    explicit = request.query_params.get("scope")
    if explicit in ("all", "my-company", "mine"):
        return explicit
    return "my-company" if session_user else "all"


def _viewer_company_id(session_user, scope: str) -> "str | None":
    """[Library 1] Resolve which company_id to apply filter_for_company with."""
    if not session_user:
        return None
    if scope == "my-company" or scope == "mine":
        return session_user.company_id
    return None   # "all" → unscoped


def _ctx(request: Request, **extra) -> dict:
    """Shared template context — every page gets counts + identity + scope."""
    counts = inventory.inventory_counts() if "counts" not in extra else extra["counts"]
    pending_count = len(store.list_submissions(status="pending"))
    ws = _ws(request)
    session_user = _session_user(request)
    scope = _scope(request, session_user)
    base = {
        "current_product": "library",
        "workspace": ws,
        "workspaces": store.list_workspaces(),
        "actor": _user(request),
        "counts": counts,
        "pending_count": pending_count,
        "use_case_count": use_cases.count(ws),
        # [Library 2] identity-aware context
        "current_session_user": session_user,
        "scope": scope,
        "viewer_company_id": _viewer_company_id(session_user, scope),
    }
    base.update(extra)
    return base


@router.get("/")
async def library_home(request: Request):
    workspace = _ws(request)
    counts = inventory.inventory_counts()
    total = sum(counts.values())
    feat = featured.pick_featured(workspace=workspace)
    last_scan = scanner.last_scan_summary()
    new_this_week = scanner.candidates_this_week()
    pending = store.list_submissions(status="pending")
    # Lead with Use Cases — top 6 by rating, then by recency
    top_use_cases = use_cases.list_all(workspace, limit=6, sort="rating")
    if not top_use_cases:
        top_use_cases = use_cases.list_all(workspace, limit=6, sort="newest")
    # Word cloud preview — real text frequency (same encoding as Use Cases page)
    home_cloud = word_cloud.cloud_for_use_cases(
        workspace=workspace, mode="frequency", dimension="keyword",
        current={}, base_url="/library/use-cases", limit=60,
    )
    return request.app.state.templates.TemplateResponse(
        request, "library/home.html",
        _ctx(request,
                  library_nav_active="home",
                  categories=inventory.CATEGORIES,
                  counts=counts,
                  total_assets=total,
                  featured=feat,
                  last_scan=last_scan,
                  new_candidates_count=new_this_week,
                  pending_count=len(pending),
                  top_use_cases=top_use_cases,
                  home_cloud=home_cloud),
    )


# ── Vetting actions ────────────────────────────────────────────────


@router.post("/{category_key}/{asset_id}/vet")
async def library_vet(request: Request, category_key: str, asset_id: str,
                            decision: str = Form("accepted"),
                            notes: str = Form("")):
    workspace = _ws(request)
    reviewer = _user(request)
    if decision == "accepted":
        store.mark_vetted(workspace, category_key, asset_id, reviewer, notes)
    else:
        store.reject(workspace, category_key, asset_id, reviewer, notes)
    return RedirectResponse(
        url=f"/library/{category_key}/{asset_id}?ws={workspace}&as={reviewer}",
        status_code=303,
    )


# ── Ratings + comments (POST) ──────────────────────────────────────


@router.post("/{category_key}/{asset_id}/rate")
async def library_rate(request: Request, category_key: str, asset_id: str,
                              stars: int = Form(...),
                              note: str = Form("")):
    workspace = _ws(request)
    rater = _user(request)
    store.add_rating(workspace, category_key, asset_id, rater, stars, note)
    return RedirectResponse(
        url=f"/library/{category_key}/{asset_id}?ws={workspace}&as={rater}",
        status_code=303,
    )


@router.post("/{category_key}/{asset_id}/comment")
async def library_comment(request: Request, category_key: str, asset_id: str,
                                  body: str = Form(...)):
    workspace = _ws(request)
    author = _user(request)
    if body.strip():
        store.add_comment(workspace, category_key, asset_id, author, body)
    return RedirectResponse(
        url=f"/library/{category_key}/{asset_id}?ws={workspace}&as={author}",
        status_code=303,
    )


# ── Submission flow ────────────────────────────────────────────────


@router.get("/submit")
async def library_submit_form(request: Request):
    return request.app.state.templates.TemplateResponse(
        request, "library/submit.html",
        _ctx(request,
                  library_nav_active="submit",
                  categories=inventory.CATEGORIES),
    )


@router.post("/submit")
async def library_submit(request: Request,
                                category: str = Form(...),
                                name: str = Form(...),
                                description: str = Form(...),
                                how_to_use: str = Form(""),
                                example: str = Form(""),
                                tags: str = Form(""),
                                source: str = Form("")):
    workspace = _ws(request)
    submitter = _user(request)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    s = store.submit(workspace, category, submitter, name, description,
                            how_to_use, example, tag_list, source)
    return RedirectResponse(
        url=f"/library/submit/{s.submission_id}?ws={workspace}&as={submitter}",
        status_code=303,
    )


@router.get("/submit/{submission_id}")
async def library_submit_thanks(request: Request, submission_id: str):
    subs = [s for s in store.list_submissions(workspace=_ws(request))
                if s.submission_id == submission_id]
    if not subs:
        raise HTTPException(404, "Submission not found")
    return request.app.state.templates.TemplateResponse(
        request, "library/submit_thanks.html",
        _ctx(request, library_nav_active="submit", submission=subs[0]),
    )


@router.get("/pending")
async def library_pending(request: Request):
    submissions = store.list_submissions(status="pending")
    return request.app.state.templates.TemplateResponse(
        request, "library/pending.html",
        _ctx(request, library_nav_active="pending", submissions=submissions),
    )


@router.post("/pending/{submission_id}/review")
async def library_review(request: Request, submission_id: str,
                                decision: str = Form(...),
                                notes: str = Form("")):
    workspace = _ws(request)
    reviewer = _user(request)
    store.review_submission(workspace, submission_id, decision, reviewer, notes)
    return RedirectResponse(
        url=f"/library/pending?ws={workspace}&as={reviewer}",
        status_code=303,
    )


# ── URL / GitHub ingestion ────────────────────────────────────────


@router.get("/ingest")
async def library_ingest_form(request: Request):
    recent = ingest.list_recent_batches(limit=10)
    return request.app.state.templates.TemplateResponse(
        request, "library/ingest.html",
        _ctx(request, library_nav_active="ingest",
                  categories=inventory.CATEGORIES,
                  recent_batches=recent),
    )


@router.post("/ingest/url")
async def library_ingest_url(request: Request, url: str = Form(...)):
    workspace = _ws(request)
    submitter = _user(request)
    h = ingest.ingest_url(workspace, submitter, url.strip())
    return RedirectResponse(
        url=f"/library/ingest/{h.batch_id}?ws={workspace}&as={submitter}",
        status_code=303,
    )


@router.post("/ingest/github")
async def library_ingest_github(request: Request,
                                        repo_url: str = Form(...),
                                        ref: str = Form("main")):
    workspace = _ws(request)
    submitter = _user(request)
    h = ingest.ingest_github_repo(workspace, submitter, repo_url.strip(), ref.strip() or "main")
    return RedirectResponse(
        url=f"/library/ingest/{h.batch_id}?ws={workspace}&as={submitter}",
        status_code=303,
    )


@router.get("/ingest/{batch_id}")
async def library_ingest_status(request: Request, batch_id: str):
    status = ingest.batch_status(batch_id)
    if status.get("error"):
        raise HTTPException(404, "Batch not found")
    return request.app.state.templates.TemplateResponse(
        request, "library/ingest_progress.html",
        _ctx(request, library_nav_active="ingest",
                  batch_id=batch_id, status=status),
    )


@router.get("/ingest/{batch_id}/json")
async def library_ingest_status_json(request: Request, batch_id: str):
    """JSON polling endpoint for live progress."""
    return JSONResponse(ingest.batch_status(batch_id))


@router.get("/ingest/{batch_id}/report")
async def library_ingest_report(request: Request, batch_id: str):
    """Post-ingest summary: every asset that landed, grouped by outcome,
    with click-through links to the new asset detail pages."""
    workspace = _ws(request)
    status = ingest.batch_status(batch_id)
    if status.get("error"):
        raise HTTPException(404, "Batch not found")
    results = status.get("all_results", [])
    # Hydrate submitted items with their pending-review submission record
    pending_subs = {s.submission_id: s
                          for s in store.list_submissions(workspace=workspace)}
    # Bucket by outcome
    submitted = [r for r in results if r["status"] == "submitted"]
    auto_vetted = [r for r in submitted if r.get("auto_vetted")]
    pending = [r for r in submitted if not r.get("auto_vetted")]
    failed = [r for r in results if r["status"] == "failed"]
    skipped = [r for r in results if r["status"] not in ("submitted", "failed")]

    # Category counts
    cat_counts: dict[str, int] = {}
    for r in submitted:
        c = r.get("category") or "unknown"
        cat_counts[c] = cat_counts.get(c, 0) + 1

    # Quality histogram
    quality_buckets = {"high": 0, "medium": 0, "low": 0, "none": 0}
    for r in submitted:
        q = r.get("quality_score") or 0
        if q >= 0.7: quality_buckets["high"] += 1
        elif q >= 0.4: quality_buckets["medium"] += 1
        elif q > 0: quality_buckets["low"] += 1
        else: quality_buckets["none"] += 1

    # Top tags across the submissions
    from collections import Counter
    tag_counter: Counter = Counter()
    for r in submitted:
        sid = r.get("submission_id")
        sub = pending_subs.get(sid)
        if sub:
            for t in sub.tags:
                tag_counter[t] += 1
    top_tags = tag_counter.most_common(20)

    # Hydrate links — point to the new asset detail / submission record
    hydrated_submitted = []
    for r in submitted:
        sid = r.get("submission_id")
        sub = pending_subs.get(sid)
        cat = r.get("category")
        # If accepted, the asset lives at /library/{cat}/sub-{sid}
        if r.get("auto_vetted") and sub and sub.asset_id:
            url = f"/library/{cat}/{sub.asset_id}?ws={workspace}"
        elif sub:
            url = f"/library/pending?ws={workspace}#" + sid
        else:
            url = ""
        hydrated_submitted.append({**r, "url": url,
                                            "tags": sub.tags if sub else [],
                                            "description": sub.description if sub else ""})

    return request.app.state.templates.TemplateResponse(
        request, "library/ingest_report.html",
        _ctx(request, library_nav_active="ingest",
                  batch_id=batch_id, status=status,
                  results=results,
                  submitted=hydrated_submitted,
                  auto_vetted_count=len(auto_vetted),
                  pending_count_batch=len(pending),
                  failed=failed, skipped=skipped,
                  cat_counts=cat_counts,
                  quality_buckets=quality_buckets,
                  top_tags=top_tags),
    )


# ── Use Cases ──────────────────────────────────────────────────────


@router.get("/use-cases")
async def use_case_index(request: Request):
    workspace = _ws(request)
    sort = request.query_params.get("sort") or "newest"
    vetted_only = request.query_params.get("vetted") == "1"
    mode = request.query_params.get("mode") or "frequency"
    dim = request.query_params.get("dim") or "keyword"

    # Active filters (preserved across nav)
    filters = {k: request.query_params[k] for k in
                  ("keyword", "industry", "complexity", "tool", "tag", "persona")
                  if request.query_params.get(k)}

    # Apply filters before sort
    all_cases = use_cases.list_all(workspace, sort=sort)
    cases = word_cloud._apply_uc_filters(all_cases, filters)
    if vetted_only:
        cases = [c for c in cases if c.vetted]

    # Build the word cloud terms
    cloud_terms = word_cloud.cloud_for_use_cases(
        workspace=workspace, mode=mode, dimension=dim, current=filters,
        base_url="/library/use-cases",
    )

    # Refinement chips appear once at least one filter is active
    chips = (word_cloud.refinement_chips_for_use_cases(
                  workspace, filters, base_url="/library/use-cases")
               if filters else [])

    return request.app.state.templates.TemplateResponse(
        request, "library/use_cases.html",
        _ctx(request, library_nav_active="use_cases",
                  cases=cases, total_cases=len(all_cases),
                  sort=sort, only_vetted=vetted_only,
                  cloud_terms=cloud_terms, chips=chips,
                  filters=filters, mode=mode, current_dimension=dim,
                  available_dimensions=[
                      ("keyword",  "Keyword (text)"),
                      ("industry", "Industry"),
                      ("persona",  "Persona"),
                      ("tool",     "Tool used"),
                      ("tag",      "Tag"),
                      ("complexity","Complexity"),
                  ],
                  available_modes=[
                      ("frequency", "Frequency"),
                      ("rating",    "Avg rating"),
                      ("freshness", "Freshness"),
                      ("vetted",    "Vetted ratio"),
                  ]),
    )


@router.get("/use-cases/{uc_id}")
async def use_case_detail(request: Request, uc_id: str):
    workspace = _ws(request)
    uc = use_cases.get(workspace, uc_id)
    if uc is None:
        raise HTTPException(404, "use case not found")
    # Hydrate each tool with its metadata for action buttons
    hydrated_tools = []
    for t in uc.tools_used:
        cat_key = t.get("category", "")
        asset_id = t.get("asset_id", "")
        meta = store.get_metadata(workspace, cat_key, asset_id)
        cat = inventory.get_category(cat_key)
        # Find the raw catalog row too (for emoji + canonical link)
        rows = inventory.load_category(cat_key) or []
        raw = next((r for r in rows
                          if (r.get("name") or r.get("id") or "") == asset_id), None)
        hydrated_tools.append({
            "category": cat_key,
            "asset_id": asset_id,
            "role": t.get("role", ""),
            "category_label": cat.label if cat else cat_key,
            "category_emoji": cat.emoji if cat else "📦",
            "meta": meta,
            "raw": raw,
            "exists": raw is not None,
        })
    ratings = use_cases.list_ratings(workspace, uc_id)
    comments = use_cases.list_comments(workspace, uc_id)
    return request.app.state.templates.TemplateResponse(
        request, "library/use_case_detail.html",
        _ctx(request, library_nav_active="use_cases",
                  uc=uc, tools=hydrated_tools,
                  ratings=ratings, comments=comments),
    )


@router.post("/use-cases/generate")
async def use_case_generate(request: Request):
    workspace = _ws(request)
    actor = _user(request)
    uc = use_case_generator.generate_one(workspace, creator=f"user:{actor}")
    return RedirectResponse(
        url=f"/library/use-cases/{uc.use_case_id}?ws={workspace}&as={actor}",
        status_code=303,
    )


@router.post("/use-cases/{uc_id}/rate")
async def use_case_rate(request: Request, uc_id: str,
                              stars: int = Form(...), note: str = Form("")):
    workspace = _ws(request)
    use_cases.add_rating(workspace, uc_id, _user(request), stars, note)
    return RedirectResponse(
        url=f"/library/use-cases/{uc_id}?ws={workspace}&as={_user(request)}",
        status_code=303,
    )


@router.post("/use-cases/{uc_id}/comment")
async def use_case_comment(request: Request, uc_id: str,
                                  body: str = Form(...)):
    workspace = _ws(request)
    if body.strip():
        use_cases.add_comment(workspace, uc_id, _user(request), body)
    return RedirectResponse(
        url=f"/library/use-cases/{uc_id}?ws={workspace}&as={_user(request)}",
        status_code=303,
    )


# ── Enrichment ─────────────────────────────────────────────────────


@router.post("/{category_key}/{asset_id}/enrich")
async def library_enrich_one(request: Request, category_key: str, asset_id: str,
                                     force: bool = False):
    workspace = _ws(request)
    actor = _user(request)
    # Find source URL from inventory
    cat = inventory.get_category(category_key)
    if cat is None:
        raise HTTPException(404, "category")
    items = inventory.load_category(cat.key)
    raw = next((it for it in items
                     if (it.get("name") or it.get("id") or "") == asset_id), None)
    if not raw:
        raise HTTPException(404, "asset")
    source_url = raw.get("source") or raw.get("source_url") or ""
    enrichment_job.enrich_asset(workspace, cat.key, asset_id, source_url,
                                          actor, force=bool(force))
    return RedirectResponse(
        url=f"/library/{cat.key}/{asset_id}?ws={workspace}&as={actor}",
        status_code=303,
    )


@router.post("/enrich/{category_key}")
async def library_enrich_category(request: Request, category_key: str,
                                          force: bool = False):
    workspace = _ws(request)
    actor = _user(request)
    if category_key != "all" and not inventory.get_category(category_key):
        raise HTTPException(404, "category")
    if category_key == "all":
        # Spawn one batch per category
        last_h = None
        for cat in inventory.CATEGORIES:
            h = ingest.enrich_category(workspace, actor, cat.key, force=bool(force))
            last_h = h
        if last_h is None:
            raise HTTPException(400, "no categories")
        return RedirectResponse(
            url=f"/library/ingest/{last_h.batch_id}?ws={workspace}&as={actor}",
            status_code=303,
        )
    h = ingest.enrich_category(workspace, actor, category_key, force=bool(force))
    return RedirectResponse(
        url=f"/library/ingest/{h.batch_id}?ws={workspace}&as={actor}",
        status_code=303,
    )


# ── Search ─────────────────────────────────────────────────────────


@router.get("/search")
async def library_search(request: Request):
    workspace = _ws(request)
    q = (request.query_params.get("q") or "").strip()
    only_vetted = request.query_params.get("vetted") == "1"
    hits = search_mod.search(q, workspace=workspace, only_vetted=only_vetted) if q else []
    return request.app.state.templates.TemplateResponse(
        request, "library/search.html",
        _ctx(request, library_nav_active="search",
                  q=q, only_vetted=only_vetted, hits=hits),
    )


# ── Scanner views ──────────────────────────────────────────────────


@router.get("/candidates")
async def library_candidates(request: Request):
    cands = scanner.list_candidates(status="new")
    last_scan = scanner.last_scan_summary()
    return request.app.state.templates.TemplateResponse(
        request, "library/candidates.html",
        _ctx(request, library_nav_active="candidates",
                  candidates=cands, last_scan=last_scan),
    )


@router.post("/candidates/scan")
async def library_run_scan(request: Request):
    workspace = _ws(request)
    summary = scanner.scan_once()
    return RedirectResponse(
        url=f"/library/candidates?ws={workspace}",
        status_code=303,
    )


# ── Per-asset detail page ─────────────────────────────────────────


@router.get("/{category_key}/{asset_id}")
async def library_asset_detail(request: Request, category_key: str, asset_id: str):
    cat = inventory.get_category(category_key)
    if cat is None:
        raise HTTPException(404, f"Unknown category: {category_key}")
    items = inventory.load_category(cat.key)
    raw = next((it for it in items
                     if (it.get("name") or it.get("id") or "") == asset_id), None)
    workspace = _ws(request)
    meta = store.get_metadata(workspace, cat.key, asset_id)
    ratings = store.list_ratings(workspace, cat.key, asset_id)
    comments = store.list_comments(workspace, cat.key, asset_id)
    linked_use_cases = use_cases.find_by_tool(workspace, cat.key, asset_id)
    return request.app.state.templates.TemplateResponse(
        request, "library/asset.html",
        _ctx(request,
                  library_nav_active=cat.key,
                  category=cat,
                  asset_id=asset_id,
                  raw=raw,
                  meta=meta,
                  ratings=ratings,
                  comments=comments,
                  linked_use_cases=linked_use_cases),
    )


# ── Category listing (with vetted filter) ─────────────────────────


@router.get("/{category_key}")
async def library_category(request: Request, category_key: str):
    cat = inventory.get_category(category_key)
    if cat is None:
        if category_key in ("mcp-servers", "mcpservers"):
            cat = inventory.get_category("mcp")
            category_key = "mcp"
    if cat is None:
        raise HTTPException(status_code=404, detail=f"Unknown category: {category_key}")
    items = inventory.load_category(cat.key)
    workspace = _ws(request)
    only_vetted = request.query_params.get("vetted") == "1"
    tag_filter = request.query_params.get("tag")
    mode = request.query_params.get("mode") or "frequency"

    # [Library 1] Per-company filter — applied BEFORE per-item vetted/tag filters
    session_user = _session_user(request)
    scope = _scope(request, session_user)
    viewer_co = _viewer_company_id(session_user, scope)
    items = inventory.filter_for_company(items, cat.key, viewer_co)

    # [Library 2] "mine" scope = items the user submitted
    if scope == "mine" and session_user:
        items = [
            it for it in items
            if store.get_metadata(workspace, cat.key,
                                          it.get("name") or it.get("id") or "")
            .submitted_by == session_user.email
        ]

    # [Library 1] approved-by filter (?approved=company:colaberry,patriot)
    approved_filter = request.query_params.get("approved", "")
    approved_companies: list[str] = []
    if approved_filter.startswith("company:"):
        approved_companies = [c.strip() for c in approved_filter[8:].split(",") if c.strip()]
    if approved_companies:
        narrowed = []
        for it in items:
            aid = it.get("name") or it.get("id") or ""
            their_approvals = tenancy.list_approvals(
                item_kind="library_asset", item_id=aid, category=cat.key,
                status="approved",
            )
            approver_companies = {a.company_id for a in their_approvals}
            if approver_companies.intersection(approved_companies):
                narrowed.append(it)
        items = narrowed

    enriched = []
    for it in items:
        asset_id = it.get("name") or it.get("id") or ""
        meta = store.get_metadata(workspace, cat.key, asset_id)
        if only_vetted and not meta.vetted:
            continue
        if tag_filter and tag_filter not in (it.get("tags") or []):
            continue
        # [Library 1] per-item approval badges — collect all companies that approved
        item_approvals = tenancy.list_approvals(
            item_kind="library_asset", item_id=asset_id,
            category=cat.key, status="approved",
        )
        enriched.append({
            **it, "_meta": meta,
            "_approving_companies": [
                {"company_id": a.company_id, "approved_at": a.approved_at,
                  "company_name": (tenancy.get_company(a.company_id).display_name
                                          if tenancy.get_company(a.company_id) else a.company_id)}
                for a in item_approvals
            ],
        })

    cat_cloud = word_cloud.cloud_for_assets(
        workspace=workspace, mode=mode, category=cat.key,
        current={"tag": tag_filter} if tag_filter else {},
        base_url=f"/library/{cat.key}", limit=50,
    )

    # List of companies the user can filter by ("other company approved")
    all_companies = [c for c in tenancy.list_companies()
                            if c.company_id != (viewer_co or "")]

    return request.app.state.templates.TemplateResponse(
        request, "library/category.html",
        _ctx(request,
                  library_nav_active=cat.key,
                  category=cat,
                  items=enriched,
                  count=len(enriched),
                  total_count=len(items),
                  only_vetted=only_vetted,
                  tag_filter=tag_filter,
                  cat_cloud=cat_cloud,
                  mode=mode,
                  approved_filter_companies=approved_companies,
                  all_companies=all_companies),
    )
