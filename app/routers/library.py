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
    ws = _ws(request)
    session_user = _session_user(request)
    scope = _scope(request, session_user)
    viewer_co = _viewer_company_id(session_user, scope)
    # Counts honor the viewer's scope so left-nav numbers match what's
    # actually rendered on the category pages.
    counts = (inventory.inventory_counts(viewer_company_id=viewer_co)
              if "counts" not in extra else extra["counts"])
    pending_count = len(store.list_submissions(status="pending"))

    # [Workflow 1] bell counter + reviewer queue count
    bell_count = 0
    queue_count = 0
    is_reviewer = False
    if session_user:
        try:
            from execution.products.library import notifications as _notif
            bell_count = _notif.unread_count_for_user(
                session_user.user_id, session_user.company_id,
            )
        except Exception:
            bell_count = 0
        try:
            if tenancy.can_review(session_user):
                is_reviewer = True
                q = tenancy.queue_counts(session_user.company_id)
                queue_count = q.get("submitted", 0) + q.get("under_review", 0)
        except Exception:
            pass

    base = {
        "current_product": "library",
        "workspace": ws,
        "workspaces": store.list_workspaces(),
        "actor": _user(request),
        "counts": counts,
        "pending_count": pending_count,
        "use_case_count": use_cases.count(ws, viewer_company_id=viewer_co),
        # [Library 2] identity-aware context
        "current_session_user": session_user,
        "scope": scope,
        "viewer_company_id": viewer_co,
        # [Workflow 1] notifications + queue
        "bell_count": bell_count,
        "queue_count": queue_count,
        "is_reviewer": is_reviewer,
    }
    base.update(extra)
    return base


# ── [Workflow 2] Follow author ───────────────────────────────────


@router.post("/follow")
async def follow_author(request: Request,
                                  target_email: str = Form(...),
                                  action: str = Form("follow")):
    from fastapi.responses import RedirectResponse
    from execution.products.library import notifications as _notif
    user = _session_user(request)
    if not user and not auth_google.is_enabled():
        user = tenancy.get_user("ali@colaberry.com")
    if not user:
        return RedirectResponse("/auth/login", status_code=303)
    target = tenancy.get_user(target_email)
    if not target:
        raise HTTPException(404, f"Unknown author: {target_email}")
    # Permission check using same can_follow_author logic
    provenance = {"author_email": target.email, "author_company": target.company_id}
    if not tenancy.can_follow_author(user, provenance):
        raise HTTPException(403, f"{target.company_id} does not allow inbound follows")
    if action == "unfollow":
        tenancy.unfollow_author(user.user_id, target.email)
    else:
        tenancy.follow_author(user.user_id, target.email)
        # Optional notification to the author that someone followed them
        _notif.emit(_notif.NotificationEvent(
            kind="follow", company_id=target.company_id,
            actor_user_id=user.user_id, target_user_id=target.user_id,
            item_kind="user", item_id=user.email, category="follows",
            summary=f"{user.display_name} ({user.company_id}) followed you",
        ))
    return RedirectResponse(
        request.headers.get("Referer", "/library/"), status_code=303,
    )


# ── [Workflow 1] Notifications inbox ─────────────────────────────


@router.get("/notifications")
async def notifications_page(request: Request):
    from execution.products.library import notifications as _notif
    user = _session_user(request)
    if not user:
        # Dev fallback
        if not auth_google.is_enabled():
            user = tenancy.get_user("ali@colaberry.com")
        if not user:
            from fastapi.responses import RedirectResponse
            return RedirectResponse("/auth/login?next=/library/notifications", status_code=303)
    items = _notif.unread_for_user(user.user_id, user.company_id)
    return request.app.state.templates.TemplateResponse(
        request, "library/notifications.html",
        _ctx(request, library_nav_active="notifications",
                 inbox=items, inbox_count=len(items),
                 inbox_user=user),
    )


@router.post("/notifications/mark-read")
async def notifications_mark_read(request: Request):
    from execution.products.library import notifications as _notif
    from fastapi.responses import RedirectResponse
    user = _session_user(request)
    if not user and not auth_google.is_enabled():
        user = tenancy.get_user("ali@colaberry.com")
    if user:
        _notif.mark_all_read(user.user_id, user.company_id)
    return RedirectResponse("/library/notifications", status_code=303)


@router.get("/")
async def library_home(request: Request):
    workspace = _ws(request)
    session_user = _session_user(request)
    scope = _scope(request, session_user)
    viewer_co = _viewer_company_id(session_user, scope)
    counts = inventory.inventory_counts(viewer_company_id=viewer_co)
    total = sum(counts.values())
    feat = featured.pick_featured(workspace=workspace)
    last_scan = scanner.last_scan_summary()
    new_this_week = scanner.candidates_this_week()
    pending = store.list_submissions(status="pending")
    # Lead with Use Cases — top 6 by rating, then by recency
    top_use_cases = use_cases.list_all(workspace, limit=6, sort="rating",
                                                          viewer_company_id=viewer_co)
    if not top_use_cases:
        top_use_cases = use_cases.list_all(workspace, limit=6, sort="newest",
                                                              viewer_company_id=viewer_co)
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
    from execution.products.library import category_schemas
    # Build a JSON-serializable map of category -> schema so the template
    # can drive client-side field show/hide via JS without a server round-
    # trip on every category dropdown change.
    schemas_json = {c.key: category_schemas.schema_for(c.key)
                              for c in inventory.CATEGORIES}
    return request.app.state.templates.TemplateResponse(
        request, "library/submit.html",
        _ctx(request,
                  library_nav_active="submit",
                  categories=inventory.CATEGORIES,
                  category_schemas_json=schemas_json),
    )


@router.post("/submit")
async def library_submit(request: Request):
    """Per-category submit. Reads ALL form fields generically (Form() with
    fixed param names couldn't handle the variable schema), validates against
    category_schemas, persists to store.submit(), and optionally auto-approves
    when LIBRARY_AUTO_APPROVE_ON_SUBMIT=1.
    """
    import os
    from execution.products.library import category_schemas
    from execution.products.library import tenancy

    form = await request.form()
    category = (form.get("category") or "").strip()
    if category not in {c.key for c in inventory.CATEGORIES}:
        raise HTTPException(400, f"unknown category: {category!r}")

    workspace = _ws(request)
    # Prefer the SSO'd session user for company attribution; fall back to
    # the legacy ?as / X-User string for compatibility.
    session_user = _session_user(request)
    submitter_email = session_user.email if session_user else _user(request)
    submitter_company = session_user.company_id if session_user else ""

    # Build the payload from every form field the schema mentions. Unknown
    # fields (e.g. the category dropdown itself) are skipped.
    schema = category_schemas.schema_for(category)
    fields = list(schema["required"]) + list(schema["optional"])
    payload: dict = {}
    for fname in fields:
        raw = (form.get(fname) or "").strip()
        if not raw:
            continue
        # List fields: textarea OR comma-separated text get split.
        if fname in {"tags", "languages", "allowed_tools"}:
            payload[fname] = category_schemas.normalize_list_field(raw, sep=",")
        elif fname in {"steps", "dependencies", "install_steps"}:
            payload[fname] = category_schemas.normalize_list_field(raw, sep="\n")
        else:
            payload[fname] = raw

    missing = category_schemas.validate_payload(category, payload)
    if missing:
        raise HTTPException(400, f"missing required fields for {category!r}: "
                                                          f"{', '.join(missing)}")

    # Pull out the fields the Submission dataclass holds at the top level;
    # everything else stays in payload.
    name = payload.pop("name", "")
    description = payload.pop("description", "")
    how_to_use = payload.pop("how_to_use", "")
    example = payload.pop("example", "")
    tags_list = payload.pop("tags", [])
    source_str = payload.pop("source", "")

    s = store.submit(
        workspace, category, submitter_email,
        name, description, how_to_use, example,
        tags_list if isinstance(tags_list, list) else [],
        source_str if isinstance(source_str, str) else "",
        payload=payload,
        owning_company_id=submitter_company,
    )

    # Piece 2: auto-approve switch. When LIBRARY_AUTO_APPROVE_ON_SUBMIT=1
    # accept the submission immediately so the asset shows up on
    # /library/<category> with no manual review step. Audit log records
    # the bypass honestly so it's traceable.
    auto_approve = (os.environ.get("LIBRARY_AUTO_APPROVE_ON_SUBMIT", "") or "").strip() in ("1", "true", "yes", "on")
    if auto_approve:
        try:
            store.review_submission(
                workspace, s.submission_id,
                decision="accepted",
                reviewer=submitter_email,
                notes="auto-approved per LIBRARY_AUTO_APPROVE_ON_SUBMIT rollout policy",
            )
            # Also record the tenancy approval row so company-scoped
            # visibility opens for the owning company.
            owner_co = submitter_company or "community"
            asset_id = f"sub-{s.submission_id}"
            try:
                tenancy.record_approval(
                    item_kind="library_asset",
                    item_id=asset_id,
                    category=category,
                    company_id=owner_co,
                    approved_by_user_id=(session_user.user_id if session_user else "system"),
                    status="approved",
                    notes="auto-approved per LIBRARY_AUTO_APPROVE_ON_SUBMIT rollout policy",
                )
            except Exception:
                # Tenancy is advisory here; failure shouldn't block the asset.
                pass
        except Exception:
            # If auto-approve fails, the asset remains in pending state --
            # the manual review path still works.
            pass

    return RedirectResponse(
        url=f"/library/submit/{s.submission_id}?ws={workspace}&as={submitter_email}",
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
    session_user = _session_user(request)
    scope = _scope(request, session_user)
    viewer_co = _viewer_company_id(session_user, scope)
    all_cases = use_cases.list_all(workspace, sort=sort,
                                                viewer_company_id=viewer_co)
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
    claude_prompt = build_use_case_prompt(uc)
    return request.app.state.templates.TemplateResponse(
        request, "library/use_case_detail.html",
        _ctx(request, library_nav_active="use_cases",
                  uc=uc, tools=hydrated_tools,
                  ratings=ratings, comments=comments,
                  claude_prompt=claude_prompt),
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


# ── Claude Code "use this asset" prompt builder ────────────────────
# Per-category prompt template. Inlined here rather than separate .j2
# files because it's a one-liner per category and centralizing keeps
# them easy to tune. Each template references the new
# colaberry_get_asset MCP tool (Piece 6); the prompt won't work until
# that tool is registered.

_CLAUDE_PROMPT_TEMPLATES = {
    "skills": (
        'Use the "{name}" skill from our Colaberry library. Call '
        'colaberry_get_asset(category="skills", asset_id="{asset_id}") '
        "to fetch its full body and how-to-use instructions, then apply "
        "it to: <YOUR TASK HERE>."
    ),
    "agents": (
        'Spawn the "{name}" agent from our Colaberry library. Call '
        'colaberry_get_asset(category="agents", asset_id="{asset_id}") '
        "to fetch its role, system prompt, and allowed tools, then run "
        "it against: <YOUR TASK HERE>."
    ),
    "prompts": (
        'Use the "{name}" prompt from our Colaberry library. Call '
        'colaberry_get_asset(category="prompts", asset_id="{asset_id}") '
        "to fetch the prompt body, then substitute your context for any "
        "{{placeholders}} and run it."
    ),
    "mcp": (
        'I want to install + use the "{name}" MCP server from our '
        'Colaberry library. Call colaberry_get_asset(category="mcp", '
        'asset_id="{asset_id}") to fetch its install command + config, '
        "then walk me through installing it in Claude Code."
    ),
    "workflows": (
        'Run the "{name}" workflow from our Colaberry library. Call '
        'colaberry_get_asset(category="workflows", asset_id="{asset_id}") '
        "to fetch its steps + invocation pattern, then execute them on: "
        "<YOUR CONTEXT HERE>."
    ),
    "capabilities": (
        'Reference the "{name}" capability from our Colaberry library. '
        'Call colaberry_get_asset(category="capabilities", '
        'asset_id="{asset_id}") and apply it to: <YOUR TASK HERE>.'
    ),
    "templates": (
        'Use the "{name}" template from our Colaberry library. Call '
        'colaberry_get_asset(category="templates", asset_id="{asset_id}") '
        "to fetch the blueprint + scaffolding notes, then bootstrap a "
        "new build against: <YOUR PROJECT NAME HERE>."
    ),
    "policies": (
        'Review the "{name}" policy from our Colaberry library. Call '
        'colaberry_get_asset(category="policies", asset_id="{asset_id}") '
        "to fetch the rule text + enforcement point, then check whether "
        "the following situation complies: <SITUATION HERE>."
    ),
    "governance": (
        'Apply the "{name}" governance rule from our Colaberry library. '
        'Call colaberry_get_asset(category="governance", '
        'asset_id="{asset_id}") and evaluate: <SITUATION HERE>.'
    ),
    "recovery": (
        'Apply the "{name}" recovery playbook from our Colaberry library. '
        'Call colaberry_get_asset(category="recovery", '
        'asset_id="{asset_id}") to fetch the trigger + mitigation, then '
        "respond to: <INCIDENT HERE>."
    ),
    "chaos": (
        'Run the "{name}" chaos drill from our Colaberry library. Call '
        'colaberry_get_asset(category="chaos", asset_id="{asset_id}") '
        "to fetch the fault scenario, then walk through it against: "
        "<TARGET SYSTEM HERE>."
    ),
    "evals": (
        'Score outputs against the "{name}" eval dataset from our '
        'Colaberry library. Call colaberry_get_asset(category="evals", '
        'asset_id="{asset_id}") to fetch the dataset + scoring method, '
        "then grade: <OUTPUTS HERE>."
    ),
    "connectors": (
        'Connect to the "{name}" integration via our Colaberry library. '
        'Call colaberry_get_asset(category="connectors", '
        'asset_id="{asset_id}") to fetch the install + how-to.'
    ),
    "adapters": (
        'Use the "{name}" tool adapter from our Colaberry library. Call '
        'colaberry_get_asset(category="adapters", asset_id="{asset_id}") '
        "to fetch the install + how-to."
    ),
}

_CLAUDE_PROMPT_FALLBACK = (
    'Use the "{name}" {category} asset from our Colaberry library. '
    'Call colaberry_get_asset(category="{category}", asset_id="{asset_id}") '
    "to fetch its full content, then apply it to: <YOUR TASK HERE>."
)


_LIVE_IN_MCP_PROMPT = (
    'The "{name}" tool is already live in your Colaberry MCP server at '
    "advisor.colaberry.ai/mcp/v1. Invoke it directly via the registered "
    "{tool_name} tool: no .mcp.json edits, no install steps. "
    "Use it now to: <YOUR TASK HERE>."
)


def _live_mcp_tool_name(asset_id: str, name: str) -> str:
    """Pick the canonical MCP tool identifier for a live-in-MCP asset.
    The asset_id is the more reliable source (lowercased, spaces to
    underscores); fall back to name. Both are expected to start with
    'colaberry_' for builtins."""
    for candidate in (asset_id, name):
        s = (candidate or "").strip().lower().replace(" ", "_")
        if s:
            return s
    return "colaberry_unknown"


def build_claude_prompt(category: str, asset_id: str, name: str,
                                            live_in_mcp: bool = False) -> str:
    """Return the one-line "Copy to your Claude Code session" prompt that
    pulls this asset into the conversation via colaberry_get_asset.

    When live_in_mcp=True on an mcp-category asset the user gets a
    different message: the tool is already exposed via their per-user
    bearer token, no install needed. Saves them from chasing install
    steps for tools they already have."""
    if live_in_mcp and category == "mcp":
        return _LIVE_IN_MCP_PROMPT.format(
            name=name or asset_id,
            tool_name=_live_mcp_tool_name(asset_id, name),
        )
    tpl = _CLAUDE_PROMPT_TEMPLATES.get(category, _CLAUDE_PROMPT_FALLBACK)
    return tpl.format(name=name or asset_id, asset_id=asset_id, category=category)


def build_use_case_prompt(uc) -> str:
    """Return a self-contained Claude Code prompt that walks Claude
    through executing this use case end-to-end. Unlike build_claude_prompt
    (which pulls an asset into context via colaberry_get_asset), a use
    case prompt is the full walkthrough inlined so the user can paste it
    into any Claude session with no other setup."""
    lines: list[str] = []
    lines.append(f'You are helping me execute the "{uc.title}" workflow.')
    lines.append("")
    if uc.summary:
        lines.append(f"Context: {uc.summary}")
        lines.append("")
    if uc.persona:
        lines.append(f"Persona this is for: {uc.persona}")
        lines.append("")
    if uc.problem:
        lines.append("Problem:")
        lines.append(uc.problem)
        lines.append("")
    if uc.solution:
        lines.append("Approach:")
        lines.append(uc.solution)
        lines.append("")
    if uc.walkthrough:
        lines.append("Walk through these steps in order. Pause between steps "
                              "if you need an input I have not given you.")
        for i, step in enumerate(uc.walkthrough, 1):
            lines.append(f"  {i}. {step}")
        lines.append("")
    if uc.tools_used:
        lines.append("Tools / assets referenced (call colaberry_get_asset to "
                              "fetch each if not already in context):")
        for t in uc.tools_used:
            cat = t.get("category", "")
            aid = t.get("asset_id", "")
            role = t.get("role", "")
            if role:
                lines.append(f"  - {cat}: {aid} -- {role}")
            else:
                lines.append(f"  - {cat}: {aid}")
        lines.append("")
    if uc.outcome_metric:
        lines.append(f"Expected outcome: {uc.outcome_metric}")
        lines.append("")
    lines.append("Begin step 1. Ask only if you need missing inputs.")
    return "\n".join(lines).strip()


# ── "Live in your Colaberry MCP Server" badge detection ───────────
# An asset is "live in MCP" when it represents a tool / capability the
# Colaberry MCP server natively provides (no install needed). The user
# wants a visible cue so they don't go install something they already
# have via the SSO + MCP-setup flow.
#
# Heuristic (no per-user telemetry; works server-side only):
#   1. Asset name or asset_id matches one of the registered colaberry_*
#      MCP tools by exact match (case-insensitive, underscores match
#      spaces).
#   2. Asset has the tag "colaberry-builtin" (admin override).
#   3. Asset source_url points at this advisor's /mcp/v1 endpoint.

def _builtin_tool_names() -> set:
    """Return the lowercase names of every colaberry_* tool the live
    MCP server exposes. Cached after first call (it's static per process)."""
    global _BUILTIN_TOOLS_CACHE
    try:
        return _BUILTIN_TOOLS_CACHE
    except NameError:
        pass
    try:
        from execution.products.library import mcp_tools
        names = {t.name.lower() for t in mcp_tools.TOOLS}
    except Exception:
        names = set()
    globals()["_BUILTIN_TOOLS_CACHE"] = names
    return names


def is_live_in_colaberry_mcp(name: str, asset_id: str,
                                                      tags: list | None = None,
                                                      source: str = "") -> bool:
    """Return True iff the asset is natively provided by the operator's
    Colaberry MCP server (no install needed)."""
    builtins = _builtin_tool_names()
    name_l = (name or "").lower().strip().replace(" ", "_")
    aid_l = (asset_id or "").lower().strip().replace(" ", "_")
    if name_l in builtins or aid_l in builtins:
        return True
    if tags:
        for t in tags:
            if (t or "").lower() == "colaberry-builtin":
                return True
    if source and "advisor.colaberry.ai/mcp" in source.lower():
        return True
    return False


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

    # [Workflow 2] Provenance — collect all companies that approved + author + visibility
    approvals = tenancy.list_approvals(
        item_kind="library_asset", item_id=asset_id,
        category=cat.key, status="approved",
    )
    provenance = {
        "approving_companies": [
            {
                "company_id": a.company_id,
                "company_name": (tenancy.get_company(a.company_id).display_name
                                          if tenancy.get_company(a.company_id) else a.company_id),
                "approved_at": a.approved_at,
                "approver_user_id": a.approved_by_user_id,
                "approver_name": (tenancy.get_user(a.approved_by_user_id).display_name
                                          if tenancy.get_user(a.approved_by_user_id) else "—"),
                "visibility": a.visibility,
                "shared_with": a.shared_with or [],
                "notes": a.notes,
            }
            for a in approvals
        ],
        "author_email": meta.submitted_by or "",
        "owning_company_id": getattr(meta, "owning_company_id", "colaberry"),
    }
    # If author is a known user, attach display_name + company
    if provenance["author_email"]:
        author_user = tenancy.get_user(provenance["author_email"])
        if author_user:
            provenance["author_name"] = author_user.display_name
            provenance["author_company"] = author_user.company_id
            author_company = tenancy.get_company(author_user.company_id)
            provenance["author_company_name"] = (author_company.display_name
                                                                          if author_company else author_user.company_id)

    # [Workflow 2] Follow-author state for the viewing user
    session_user = _session_user(request)
    follow_state = None
    if session_user and provenance.get("author_company"):
        follow_state = {
            "can_follow": tenancy.can_follow_author(session_user, provenance),
            "already_following": tenancy.is_following(
                follower_user_id=session_user.user_id,
                target_email=provenance["author_email"],
            ),
            "target_email": provenance["author_email"],
            "target_name": provenance.get("author_name", ""),
        }

    live_in_mcp = is_live_in_colaberry_mcp(
        name=(meta.name if meta else (raw.get("name") or asset_id)),
        asset_id=asset_id,
        tags=(raw.get("tags") if raw else []) or (meta.tags if meta else []),
        source=(raw.get("source") if raw else "") or (meta.source if meta else ""),
    )

    claude_prompt = build_claude_prompt(
        cat.key, asset_id,
        (meta.name if meta and meta.name else (raw.get("name") or asset_id)),
        live_in_mcp=live_in_mcp,
    )

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
                  linked_use_cases=linked_use_cases,
                  provenance=provenance,
                  follow_state=follow_state,
                  claude_prompt=claude_prompt,
                  live_in_mcp=live_in_mcp),
    )


# ── [Workflow 3b] Install to workspace ────────────────────────────


@router.post("/install/{category}/{asset_id:path}")
async def library_install(request: Request, category: str, asset_id: str):
    """Open a PR in the user's workspace_repo installing this asset
    (and its direct dependencies). Auth required, workspace_repo required.

    Form fields:
      subscribe  -- if "on", record an ItemSubscription so the user gets
                    an upgrade PR when the source asset is bumped.

    Live-in-Colaberry-MCP assets refuse server-side (gap 3 decision):
    we do not write a bearer-bound .mcp.json entry. The UI already
    suppresses the button; this endpoint enforces it defensively.
    """
    session_user = _session_user(request)
    if session_user is None:
        from urllib.parse import quote
        full = request.url.path + ("?" + request.url.query if request.url.query else "")
        return RedirectResponse(
            url=f"/auth/login?next={quote(full, safe='')}",
            status_code=303,
        )
    if not session_user.workspace_repo:
        raise HTTPException(
            status_code=400,
            detail=("No workspace repo configured. The admin needs to "
                          "provision your workspace before you can install."),
        )

    form = await request.form()
    subscribe = form.get("subscribe") in ("on", "true", "1", "yes")

    from execution.products.library import workspace_install
    result = workspace_install.open_install_pr(
        session_user, category, asset_id,
        workspace=_ws(request),
        subscribe=subscribe,
        triggered_by=f"web:{session_user.email}",
    )

    if result.status == "opened" and result.pr_url:
        # PR opened -- redirect the user to the GitHub review page.
        # The form has target="_blank" so this lands in a new tab.
        return RedirectResponse(url=result.pr_url, status_code=303)

    # Anything else -- render an error page with the InstallResult so
    # the user can see why and what (if anything) landed.
    return request.app.state.templates.TemplateResponse(
        request,
        "library/install_error.html",
        _ctx(request,
                  result=result,
                  category=category,
                  asset_id=asset_id,
                  back_url=(f"/library/{category}/{asset_id}"
                                  if category not in ("use_case", "use_cases")
                                  else f"/library/use-cases/{asset_id}")),
        status_code=400 if result.status == "refused" else 500,
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
        viewer_company_id=viewer_co,
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
