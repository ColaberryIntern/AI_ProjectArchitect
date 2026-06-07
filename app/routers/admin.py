"""[Admin 1 + Admin 3] Admin console — companies + users + approval audit.

Routes (all under /admin/):
    GET  /admin/                    — admin home (link grid)
    GET  /admin/companies           — list of all tenants
    GET  /admin/companies/new       — add-new-company form (super_admin only)
    POST /admin/companies/new       — create company
    GET  /admin/companies/{slug}    — company detail + edit
    POST /admin/companies/{slug}/suspend — soft-delete
    GET  /admin/users               — list of users in the current tenant (or all if super_admin)
    GET  /admin/users/new           — provisioning form
    POST /admin/users/new           — create user (triggers Provision 1 + 2 hooks)
    GET  /admin/users/{user_id}     — detail: roles, scopes, workspace repo, audit
    POST /admin/users/{user_id}/role — update roles (admin only)

Auth: all routes require a logged-in user with `admin` role for their
company. `/admin/companies/*` create routes require platform-level
super-admin (a Colaberry-tenant user with `admin` role).
"""

from __future__ import annotations

import os
import urllib.parse
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from execution.products.library import auth_google, notifications, tenancy, vault

router = APIRouter(prefix="/admin")


# ── Auth helpers ───────────────────────────────────────────────


def _current_user(request: Request) -> tenancy.User | None:
    cookie = request.cookies.get(auth_google.SESSION_COOKIE_NAME)
    return auth_google.current_user_from_cookie(cookie)


def _require_admin(request: Request) -> tenancy.User:
    user = _current_user(request)
    if not user or "admin" not in user.roles:
        # Dev mode: when SSO is disabled, treat as super_admin
        # (so internal ops can use the console while OAuth isn't configured).
        if not auth_google.is_enabled():
            ali = tenancy.get_user("ali@colaberry.com")
            if ali:
                return ali
        raise HTTPException(401, "admin role required")
    return user


def _require_super_admin(request: Request) -> tenancy.User:
    user = _require_admin(request)
    if user.company_id != "colaberry":
        raise HTTPException(403, "super_admin (Colaberry-tenant admin) required")
    return user


def _audit(actor_id: str, action: str, target: str, notes: str = "") -> None:
    """Lightweight admin audit (separate from the per-user audit logs).
    Stored alongside the tenant data under output/library/_tenants/admin_audit.jsonl
    """
    import json, time
    p = tenancy._root() / "admin_audit.jsonl"
    row = {"actor_id": actor_id, "action": action, "target": target,
              "notes": notes, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


# ── Templates context shim ─────────────────────────────────────


def _ctx(request: Request, **extra) -> dict:
    """Admin pages share Library's CSS tokens; reuse the same base layout
    but with admin nav. For v1 we render JSON-ish HTML pages that are
    minimal but functional. A future ticket can polish the UI."""
    user = _current_user(request)
    base = {
        "current_user": user,
        "company": tenancy.get_company(user.company_id) if user else None,
        "is_super_admin": bool(user and user.company_id == "colaberry"
                                       and "admin" in user.roles),
    }
    base.update(extra)
    return base


# ── Home ──────────────────────────────────────────────────────


@router.get("/")
async def admin_home(request: Request):
    user = _require_admin(request)
    return request.app.state.templates.TemplateResponse(
        request, "admin/home.html",
        _ctx(request, page_title="Admin Console"),
    )


# ── Companies ─────────────────────────────────────────────────


@router.get("/companies")
async def companies_list(request: Request):
    _require_admin(request)
    companies = tenancy.list_companies(active_only=False)
    rows = []
    for c in companies:
        users = tenancy.list_users(company_id=c.company_id)
        approvals = tenancy.list_approvals(company_id=c.company_id, status="approved")
        rows.append({
            "company": c,
            "user_count": len(users),
            "approval_count": len(approvals),
        })
    return request.app.state.templates.TemplateResponse(
        request, "admin/companies_list.html",
        _ctx(request, page_title="Companies", rows=rows),
    )


@router.get("/companies/new")
async def company_new_form(request: Request):
    _require_super_admin(request)
    return request.app.state.templates.TemplateResponse(
        request, "admin/company_new.html",
        _ctx(request, page_title="Add new company"),
    )


@router.post("/companies/new")
async def company_new(request: Request,
                            company_id: str = Form(...),
                            display_name: str = Form(...),
                            plan: str = Form("free"),
                            default_visibility: str = Form("same-company-only"),
                            primary_domain: str = Form(""),
                            notes: str = Form("")):
    admin = _require_super_admin(request)
    if tenancy.get_company(company_id):
        raise HTTPException(409, f"company_id '{company_id}' already exists")
    c = tenancy.Company(
        company_id=company_id, display_name=display_name, plan=plan,
        default_visibility=default_visibility, notes=notes,
    )
    tenancy.upsert_company(c)
    _audit(admin.user_id, "company.create",
              target=company_id,
              notes=f"plan={plan}, vis={default_visibility}, domain={primary_domain}")

    # If a primary_domain was provided, append to library_tenant_domains.json
    if primary_domain:
        try:
            import json
            cfg = auth_google._load_tenant_domains()
            cfg.setdefault("mappings", []).append({
                "domain": primary_domain.lower().strip(),
                "company_id": company_id,
                "default_roles": ["consumer"],
                "auto_provision": True,
            })
            auth_google.TENANT_DOMAINS_PATH.write_text(
                json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception:
            pass

    return RedirectResponse(url=f"/admin/companies/{company_id}", status_code=303)


@router.get("/companies/{company_id}")
async def company_detail(request: Request, company_id: str):
    _require_admin(request)
    c = tenancy.get_company(company_id)
    if not c:
        raise HTTPException(404, "company not found")
    users = tenancy.list_users(company_id=company_id, active_only=False)
    approvals = tenancy.list_approvals(company_id=company_id)
    return request.app.state.templates.TemplateResponse(
        request, "admin/company_detail.html",
        _ctx(request, page_title=f"Company: {c.display_name}",
                  company_row=c, users=users, approvals=approvals),
    )


@router.post("/companies/{company_id}/suspend")
async def company_suspend(request: Request, company_id: str):
    admin = _require_super_admin(request)
    c = tenancy.deactivate_company(company_id)
    if not c:
        raise HTTPException(404, "company not found")
    _audit(admin.user_id, "company.suspend", target=company_id)
    return RedirectResponse(url="/admin/companies", status_code=303)


# ── Users ─────────────────────────────────────────────────────


@router.get("/users")
async def users_list(request: Request, company_id: Optional[str] = None):
    user = _require_admin(request)
    scope = company_id or (None if user.company_id == "colaberry" else user.company_id)
    users = tenancy.list_users(company_id=scope, active_only=False)
    return request.app.state.templates.TemplateResponse(
        request, "admin/users_list.html",
        _ctx(request, page_title="Users", users=users, company_id=scope),
    )


@router.get("/users/new")
async def user_new_form(request: Request):
    _require_admin(request)
    companies = tenancy.list_companies()
    return request.app.state.templates.TemplateResponse(
        request, "admin/user_new.html",
        _ctx(request, page_title="Provision new user", companies=companies),
    )


@router.post("/users/new")
async def user_new(request: Request,
                          email: str = Form(...),
                          display_name: str = Form(...),
                          company_id: str = Form(...),
                          roles: str = Form("consumer"),
                          create_workspace_repo: bool = Form(False)):
    admin = _require_admin(request)
    # Non-super-admins can only provision into their own company
    if admin.company_id != "colaberry" and company_id != admin.company_id:
        raise HTTPException(403, "can only provision users in your own company")
    if tenancy.get_user(email):
        raise HTTPException(409, f"user with email {email} already exists")
    roles_list = [r.strip() for r in roles.split(",") if r.strip()]
    new_user = tenancy.User(
        user_id=tenancy._new_id("usr"),
        email=email.lower(), display_name=display_name,
        company_id=company_id, roles=roles_list or ["consumer"],
    )
    tenancy.upsert_user(new_user)
    _audit(admin.user_id, "user.create",
              target=new_user.user_id,
              notes=f"email={email} company={company_id} roles={roles_list}")

    # If create_workspace_repo, trigger Provision 1 via the workspaces module.
    if create_workspace_repo:
        try:
            from execution.products.library import workspaces
            res = workspaces.provision_user_workspace(
                new_user, admin_actor_id=admin.user_id, dry_run=False)
            if res.get("repo_url"):
                new_user.workspace_repo = res["repo_url"]
                tenancy.upsert_user(new_user)
            _audit(admin.user_id, "workspace.provision",
                      target=new_user.user_id, notes=str(res)[:300])
        except Exception as e:
            _audit(admin.user_id, "workspace.provision_failed",
                      target=new_user.user_id, notes=f"{type(e).__name__}: {e}")

    # Op 2 — Provision the user's personal Basecamp project unconditionally.
    # Best-effort: a BC API failure logs + continues rather than failing user creation.
    # Idempotent — find_personal_project() reuses an existing project by name.
    try:
        from execution.products.library import personal_bc_provisioner
        bc_token = os.environ.get("BASECAMP_ACCESS_TOKEN", "").strip()
        bc_account_id = os.environ.get("BASECAMP_ACCOUNT_ID", "").strip()
        if bc_token and bc_account_id:
            bc_res = personal_bc_provisioner.provision_user_personal_bc(
                user_email=new_user.email,
                display_name=new_user.display_name,
                account_id=bc_account_id,
                bc_token=bc_token,
            )
            if bc_res.project_id:
                new_user.personal_bc_project_id = str(bc_res.project_id)
                if bc_res.todolist_id:
                    new_user.personal_bc_todolist_id = str(bc_res.todolist_id)
                tenancy.upsert_user(new_user)
                # Refresh .claude/identity.txt in the workspace with the BC id
                # so the SessionStart hook can anchor tickets correctly (Op 2).
                if new_user.workspace_repo:
                    try:
                        from execution.products.library import workspaces as _ws
                        repo_path = new_user.workspace_repo.replace("https://github.com/", "").strip("/")
                        _ws.update_workspace_identity(
                            new_user, repo_path,
                            tenant_id=getattr(new_user, "tenant_id", None))
                    except Exception as ie:
                        _audit(admin.user_id, "workspace.identity_refresh_failed",
                                  target=new_user.user_id,
                                  notes=f"{type(ie).__name__}: {ie}")
            _audit(admin.user_id, "bc.personal_project.provision",
                      target=new_user.user_id,
                      notes=(f"action={bc_res.action} "
                                 f"project_id={bc_res.project_id} "
                                 f"name={bc_res.name!r} "
                                 f"error={bc_res.error!r}")[:300])
        else:
            _audit(admin.user_id, "bc.personal_project.skipped",
                      target=new_user.user_id,
                      notes="BASECAMP_ACCESS_TOKEN or BASECAMP_ACCOUNT_ID not set")
    except Exception as e:
        _audit(admin.user_id, "bc.personal_project.failed",
                  target=new_user.user_id, notes=f"{type(e).__name__}: {e}")

    # Phase 4 onboarding extract: if env-configured, extract a canonical
    # welcome BC ticket into a per-user `welcome-<slug>` directive in the
    # library. Gated on ONBOARDING_WELCOME_BC_ID + ONBOARDING_WELCOME_BUCKET_ID
    # so the engine can be deployed cold and turned on later by setting the
    # env vars to a real BC todo + project id.
    try:
        welcome_bc_id = os.environ.get("ONBOARDING_WELCOME_BC_ID", "").strip()
        welcome_bucket_id = os.environ.get("ONBOARDING_WELCOME_BUCKET_ID", "").strip()
        if welcome_bc_id and welcome_bucket_id:
            from execution.products.library import skill_extractor, workspaces as _ws_for_slug
            user_slug = _ws_for_slug.username_slug(new_user.email)
            ext_res = skill_extractor.extract(
                source_kind="bc_ticket",
                bc_id=welcome_bc_id,
                output_type=os.environ.get("ONBOARDING_WELCOME_OUTPUT_TYPE", "directive"),
                slug=f"welcome-{user_slug}",
                commit=True,
                bucket_id=welcome_bucket_id,
                bc_token=os.environ.get("BASECAMP_ACCESS_TOKEN", ""),
                account_id=os.environ.get("BASECAMP_ACCOUNT_ID", ""),
                created_by=admin.user_id,
            )
            if ext_res.get("ok"):
                _audit(admin.user_id, "onboarding.welcome_extract",
                          target=new_user.user_id,
                          notes=(f"slug={ext_res.get('slug')} "
                                     f"branch={ext_res.get('branch')} "
                                     f"file={ext_res.get('file_path')}")[:300])
            else:
                _audit(admin.user_id, "onboarding.welcome_extract_failed",
                          target=new_user.user_id,
                          notes=f"error={ext_res.get('error', '?')}"[:300])
        else:
            _audit(admin.user_id, "onboarding.welcome_extract_skipped",
                      target=new_user.user_id,
                      notes="ONBOARDING_WELCOME_BC_ID / _BUCKET_ID not set")
    except Exception as e:
        _audit(admin.user_id, "onboarding.welcome_extract_error",
                  target=new_user.user_id, notes=f"{type(e).__name__}: {e}")

    return RedirectResponse(url=f"/admin/users/{new_user.user_id}", status_code=303)


@router.get("/users/{user_id}")
async def user_detail(request: Request, user_id: str):
    _require_admin(request)
    u = tenancy.get_user(user_id)
    if not u:
        raise HTTPException(404, "user not found")
    scopes = tenancy.current_scopes(u.user_id)
    creds = vault.list_for_user(u.user_id, caller_id="admin-ui")
    return request.app.state.templates.TemplateResponse(
        request, "admin/user_detail.html",
        _ctx(request, page_title=u.display_name,
                  target_user=u, scopes=scopes, creds=creds),
    )


@router.post("/users/{user_id}/bc-ai")
async def user_set_bc_ai(request: Request, user_id: str,
                                          bc_ai_user_email: str = Form(...),
                                          bc_ai_user_id: int = Form(...),
                                          bc_ai_oauth_token: str = Form("")):
    """Record the user's BC AI persona after manual provisioning.

    Per Phase 8.1: each Colaberry user needs a "<Name> AI" BC user account so
    Claude's BC writes appear authored by their AI persona, not by the shared
    CB System bot. The actual BC account creation is manual (admin invites
    via email, accepts on the AI mailbox). Once that's done, admin POSTs here
    with the BC user id + optionally the OAuth token to store in the vault.

    Without a stored token, BC writes for this user fall back to the shared
    CB System token. With it, writes appear as the AI persona.
    """
    admin = _require_admin(request)
    u = tenancy.get_user(user_id)
    if not u:
        raise HTTPException(404, "user not found")
    u.bc_ai_user_email = bc_ai_user_email.strip().lower()
    u.bc_ai_user_id = int(bc_ai_user_id)
    u.bc_ai_provisioned_at = datetime.now().isoformat()
    tenancy.upsert_user(u)
    # If an OAuth token was supplied, stash it in the vault keyed by user.
    if bc_ai_oauth_token.strip():
        try:
            vault.store_secret(
                u.user_id, "basecamp_ai", bc_ai_oauth_token.strip(),
                ttl_days=14, actor_id=admin.user_id,
            )
        except Exception as e:
            _audit(admin.user_id, "bc_ai.token_store_failed",
                          target=user_id, notes=f"{type(e).__name__}: {e}")
    _audit(admin.user_id, "bc_ai.provisioned",
              target=user_id,
              notes=f"email={bc_ai_user_email} bc_id={bc_ai_user_id} token_set={bool(bc_ai_oauth_token.strip())}")
    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/role")
async def user_set_roles(request: Request, user_id: str,
                                  roles: str = Form(...)):
    admin = _require_admin(request)
    u = tenancy.get_user(user_id)
    if not u:
        raise HTTPException(404, "user not found")
    if admin.company_id != "colaberry" and u.company_id != admin.company_id:
        raise HTTPException(403, "can only modify users in your own company")
    new_roles = [r.strip() for r in roles.split(",") if r.strip()]
    u.roles = new_roles or ["consumer"]
    tenancy.upsert_user(u)
    _audit(admin.user_id, "user.set_roles", target=user_id,
              notes=f"roles={new_roles}")
    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=303)


# ── Admin 2 — Tools-access matrix ────────────────────────────


@router.get("/users/{user_id}/scopes")
async def user_scopes(request: Request, user_id: str):
    _require_admin(request)
    u = tenancy.get_user(user_id)
    if not u:
        raise HTTPException(404, "user not found")
    scopes = tenancy.current_scopes(u.user_id)
    creds = {c.tool_name: c for c in vault.list_for_user(u.user_id)}
    # All known tools (admin can grant any subset)
    all_tools = ["gmail", "calendar", "basecamp", "ccpp",
                     "github", "mandrill", "slack"]
    rows = []
    for t in all_tools:
        meta = creds.get(t)
        rows.append({
            "tool": t,
            "granted": t in scopes,
            "credential_status": meta.status if meta else "no-credential",
            "last_rotated_at": meta.last_rotated_at if meta else None,
            "days_remaining": vault.days_until_expiry(u.user_id, t),
            "ttl_days": meta.ttl_days if meta else None,
        })
    return request.app.state.templates.TemplateResponse(
        request, "admin/user_scopes.html",
        _ctx(request, page_title=f"Tools — {u.display_name}",
                  target_user=u, rows=rows, all_tools=all_tools),
    )


@router.post("/users/{user_id}/scopes/grant")
async def grant_user_scope(request: Request, user_id: str,
                                    tool: str = Form(...)):
    admin = _require_admin(request)
    tenancy.grant_scope(user_id, tool, granted_by_user_id=admin.user_id)
    _audit(admin.user_id, "scope.grant",
              target=user_id, notes=f"tool={tool}")
    return RedirectResponse(url=f"/admin/users/{user_id}/scopes", status_code=303)


@router.post("/users/{user_id}/scopes/revoke")
async def revoke_user_scope(request: Request, user_id: str,
                                      tool: str = Form(...)):
    admin = _require_admin(request)
    tenancy.revoke_scope(user_id, tool, revoked_by_user_id=admin.user_id)
    # Also revoke any stored credential
    vault.revoke(user_id, tool, caller_id=admin.user_id, reason="scope revoked")
    _audit(admin.user_id, "scope.revoke", target=user_id, notes=f"tool={tool}")
    return RedirectResponse(url=f"/admin/users/{user_id}/scopes", status_code=303)


@router.post("/users/{user_id}/scopes/credential")
async def set_user_credential(request: Request, user_id: str,
                                        tool: str = Form(...),
                                        plaintext_token: str = Form(...),
                                        ttl_days: Optional[int] = Form(None),
                                        notes: str = Form("")):
    admin = _require_admin(request)
    # Ensure scope is granted before storing token
    if tool not in tenancy.current_scopes(user_id):
        tenancy.grant_scope(user_id, tool, granted_by_user_id=admin.user_id)
    vault.store_secret(user_id, tool, plaintext_token,
                            caller_id=admin.user_id, ttl_days=ttl_days, notes=notes)
    _audit(admin.user_id, "credential.set",
              target=user_id, notes=f"tool={tool} ttl={ttl_days}")
    return RedirectResponse(url=f"/admin/users/{user_id}/scopes", status_code=303)


# ════════════════════════════════════════════════════════════════════
# [Workflow 1] Per-company moderation queue
# ════════════════════════════════════════════════════════════════════


def _require_reviewer_for(request: Request, company_id: str) -> tenancy.User:
    """A user is a reviewer for company X if they're an admin in X,
    OR if they're a Colaberry super_admin (cross-tenant override)."""
    user = _require_admin(request)
    if user.company_id == company_id:
        return user
    if user.company_id == "colaberry":  # super_admin override
        return user
    raise HTTPException(403, f"reviewer authority for {company_id} required")


@router.get("/{company_id}/queue")
async def moderation_queue(request: Request, company_id: str,
                                          status: str = "submitted,under_review"):
    reviewer = _require_reviewer_for(request, company_id)
    co = tenancy.get_company(company_id)
    if not co:
        raise HTTPException(404, f"Unknown company: {company_id}")
    status_filter = tuple(s.strip() for s in status.split(",") if s.strip())
    items = tenancy.queue_for_company(company_id, status_filter=status_filter)
    counts = tenancy.queue_counts(company_id)

    # enrich each row with author display name + last transition
    enriched = []
    for a in items:
        author = tenancy.get_user(a.approved_by_user_id)
        transitions = tenancy.list_transitions(
            item_kind=a.item_kind, item_id=a.item_id,
            category=a.category, company_id=a.company_id,
        )
        enriched.append({
            "approval": a,
            "author_name": author.display_name if author else a.approved_by_user_id,
            "author_email": author.email if author else "",
            "transitions": transitions,
        })
    return request.app.state.templates.TemplateResponse(
        request, "admin/moderation_queue.html",
        _ctx(request, company=co, items=enriched, counts=counts,
                 status_filter=status_filter, reviewer=reviewer),
    )


@router.post("/{company_id}/queue/{item_id}/claim")
async def queue_claim(request: Request, company_id: str, item_id: str,
                              category: str = Form(...),
                              item_kind: str = Form("library_asset")):
    reviewer = _require_reviewer_for(request, company_id)
    tenancy.claim_for_review(item_kind=item_kind, item_id=item_id,
                                          category=category, company_id=company_id,
                                          reviewer_user_id=reviewer.user_id)
    _audit(reviewer.user_id, "queue.claim",
              target=f"{company_id}/{category}/{item_id}", notes="")
    return RedirectResponse(url=f"/admin/{company_id}/queue", status_code=303)


@router.post("/{company_id}/queue/{item_id}/decide")
async def queue_decide(request: Request, company_id: str, item_id: str,
                                category: str = Form(...),
                                item_kind: str = Form("library_asset"),
                                decision: str = Form(...),
                                notes: str = Form(""),
                                visibility: Optional[str] = Form(None)):
    reviewer = _require_reviewer_for(request, company_id)
    if decision not in ("approved", "rejected", "changes_requested"):
        raise HTTPException(400, f"bad decision {decision}")
    ev = tenancy.decide_review(item_kind=item_kind, item_id=item_id,
                                              category=category, company_id=company_id,
                                              reviewer_user_id=reviewer.user_id,
                                              decision=decision, notes=notes,
                                              visibility=visibility)
    # Notify the original author
    transitions = tenancy.list_transitions(
        item_kind=item_kind, item_id=item_id, category=category,
        company_id=company_id,
    )
    # first transition's actor is the author
    if transitions:
        author_id = transitions[0].get("actor_id", reviewer.user_id)
        notifications.notify_decision(
            company_id=company_id, reviewer_user_id=reviewer.user_id,
            author_user_id=author_id, item_kind=item_kind, item_id=item_id,
            category=category, decision=decision, notes=notes,
        )
    _audit(reviewer.user_id, f"queue.{decision}",
              target=f"{company_id}/{category}/{item_id}", notes=notes)
    return RedirectResponse(url=f"/admin/{company_id}/queue", status_code=303)


# [Workflow 2] Per-company sharing policy + bulk visibility upgrade


@router.post("/companies/{slug}/sharing")
async def update_sharing_policy(request: Request, slug: str,
                                            allow_cross_company_shares: str = Form("off"),
                                            allow_inbound_follows: str = Form("on")):
    """Toggle a company's cross-company share + follow policies."""
    admin = _require_admin(request)
    co = tenancy.get_company(slug)
    if not co:
        raise HTTPException(404, f"Unknown company: {slug}")
    # Authorisation: only company's own admin or super_admin
    if admin.company_id != slug and admin.company_id != "colaberry":
        raise HTTPException(403, "not your company")
    co.allow_cross_company_shares = (allow_cross_company_shares == "on")
    co.allow_inbound_follows = (allow_inbound_follows == "on")
    tenancy.upsert_company(co)
    _audit(admin.user_id, "company.sharing.update", target=slug,
              notes=f"cross_co={co.allow_cross_company_shares},follows={co.allow_inbound_follows}")
    return RedirectResponse(url=f"/admin/companies/{slug}", status_code=303)


@router.post("/{company_id}/queue/{item_id}/share")
async def upgrade_share(request: Request, company_id: str, item_id: str,
                                category: str = Form(...),
                                item_kind: str = Form("library_asset"),
                                new_visibility: str = Form(...),
                                shared_with: str = Form(""),
                                notes: str = Form("")):
    admin = _require_reviewer_for(request, company_id)
    if new_visibility not in ("same-company-only", "shared-public", "shared-with-allowlist"):
        raise HTTPException(400, f"bad visibility {new_visibility}")
    allowlist = [s.strip() for s in shared_with.split(",") if s.strip()]
    try:
        tenancy.upgrade_item_visibility(
            item_kind=item_kind, item_id=item_id, category=category,
            company_id=company_id, admin_user_id=admin.user_id,
            new_visibility=new_visibility, shared_with=allowlist, notes=notes,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    _audit(admin.user_id, "item.visibility.upgrade",
              target=f"{company_id}/{category}/{item_id}",
              notes=f"new_visibility={new_visibility} allowlist={allowlist}")
    return RedirectResponse(url=f"/admin/{company_id}/queue?status=approved",
                                       status_code=303)


# Author-facing: submit a draft to the queue (called from library submit form)
@router.post("/{company_id}/queue/submit")
async def queue_submit(request: Request, company_id: str,
                                item_id: str = Form(...),
                                category: str = Form(...),
                                item_kind: str = Form("library_asset"),
                                notes: str = Form("")):
    user = _current_user(request)
    if not user or user.company_id != company_id:
        # Dev fallback: when SSO disabled, allow as ali for colaberry
        if not auth_google.is_enabled() and company_id == "colaberry":
            user = tenancy.get_user("ali@colaberry.com")
        else:
            raise HTTPException(401, "must be logged in as a member of this company")
    tenancy.submit_for_review(item_kind=item_kind, item_id=item_id,
                                          category=category, company_id=company_id,
                                          author_user_id=user.user_id, notes=notes)
    notifications.notify_submission(company_id=company_id,
                                                       author_user_id=user.user_id,
                                                       item_kind=item_kind, item_id=item_id,
                                                       category=category)
    _audit(user.user_id, "queue.submit",
              target=f"{company_id}/{category}/{item_id}", notes=notes)
    return RedirectResponse(url=f"/admin/{company_id}/queue?status=submitted",
                                       status_code=303)


# ── My Day — AI clone identity setup ────────────────────────────────


@router.get("/users/{user_id}/ai-clone")
async def ai_clone_form(request: Request, user_id: str):
    """Paste-form to register a user's Basecamp AI clone identity + token.

    Phase D: manual paste. Phase E (next session) replaces this with a real
    BC OAuth flow the admin (or user) walks through in-app.
    """
    _require_admin(request)
    u = tenancy.get_user(user_id)
    if not u:
        raise HTTPException(404, "user not found")
    # Is there already a vault credential for basecamp_ai_clone?
    existing = next(
        (c for c in vault.list_for_user(u.user_id, caller_id="admin-ui")
         if c.tool_name == "basecamp_ai_clone"),
        None,
    )
    # Compute the BC AI account provisioning status for the new
    # admin block. Best-effort: status_for_user touches BC + vault and
    # may fail in dev; an empty status dict still renders the form.
    bc_ai_status = {}
    try:
        from execution.products.library import basecamp_provisioning
        bc_ai_status = basecamp_provisioning.status_for_user(u)
    except Exception as e:
        bc_ai_status = {"error": f"{type(e).__name__}: {e}"}

    provision_msg = request.query_params.get("provision_msg") or ""
    provision_error = request.query_params.get("provision_error") or ""

    return request.app.state.templates.TemplateResponse(
        request, "admin/user_ai_clone.html",
        _ctx(request, page_title=f"AI clone — {u.display_name}",
             target_user=u, existing_credential=existing,
             bc_ai_status=bc_ai_status,
             provision_msg=provision_msg,
             provision_error=provision_error),
    )


@router.get("/bc-ai-rollout")
async def bc_ai_rollout_dashboard(request: Request):
    """Fleet-view of every user's BC AI provisioning status.

    One row per user in the admin's tenant; columns surface where each
    person is in the 4-step pipeline (personal project provisioned,
    human BC OAuth granted, AI account invited, AI OAuth granted).
    Bulk "Provision all unprovisioned" action lives at the top.
    """
    admin = _require_admin(request)
    from execution.products.library import basecamp_provisioning
    # Same-company users only unless the admin is a Colaberry super_admin.
    users = [u for u in tenancy.list_users()
                      if admin.company_id == "colaberry"
                      or u.company_id == admin.company_id]
    users.sort(key=lambda u: (u.company_id, u.email))
    rows = []
    for u in users:
        try:
            st = basecamp_provisioning.status_for_user(u)
        except Exception as e:
            st = {"error": str(e), "state": "error"}
        rows.append({
            "user": u,
            "status": st,
        })

    msg = request.query_params.get("msg") or ""
    err = request.query_params.get("err") or ""
    return request.app.state.templates.TemplateResponse(
        request, "admin/bc_ai_rollout.html",
        _ctx(request, page_title="BC AI rollout",
                  rows=rows, bulk_msg=msg, bulk_err=err),
    )


@router.post("/bc-ai-rollout/provision-all")
async def bc_ai_rollout_provision_all(request: Request):
    """Provision every not_provisioned user in one batch. Returns a
    summary in the redirect's query string."""
    admin = _require_admin(request)
    from execution.products.library import basecamp_provisioning
    users = [u for u in tenancy.list_users()
                      if admin.company_id == "colaberry"
                      or u.company_id == admin.company_id]
    provisioned: list[str] = []
    skipped: list[str] = []
    errored: list[str] = []
    for u in users:
        try:
            st = basecamp_provisioning.status_for_user(u)
        except Exception:
            errored.append(u.email + " (status-check failed)")
            continue
        if st.get("state") != "not_provisioned":
            skipped.append(f"{u.email}({st.get('state')})")
            continue
        result = basecamp_provisioning.provision_bc_ai_account(u)
        if result.ok:
            provisioned.append(f"{u.email}->{result.bc_user_email}")
            _audit(admin.user_id, "bc_ai.provisioned_via_api",
                          target=u.user_id,
                          notes=(f"bulk; bc_user_id={result.bc_user_id} "
                                      f"email={result.bc_user_email}"))
        else:
            errored.append(f"{u.email}({result.error_code})")

    parts = []
    if provisioned:
        parts.append(f"provisioned {len(provisioned)}: " + "; ".join(provisioned))
    if skipped:
        parts.append(f"skipped {len(skipped)}")
    if errored:
        parts.append(f"errored {len(errored)}: " + "; ".join(errored))
    msg = " | ".join(parts) or "nothing to do"
    return RedirectResponse(
        url=f"/admin/bc-ai-rollout?msg={urllib.parse.quote(msg)}",
        status_code=303,
    )


@router.get("/bc-ai-rollout.json")
async def bc_ai_rollout_json(request: Request):
    """JSON view of the same fleet matrix. Useful for a future user-
    facing dashboard widget or for scripted checks."""
    admin = _require_admin(request)
    from execution.products.library import basecamp_provisioning
    users = [u for u in tenancy.list_users()
                      if admin.company_id == "colaberry"
                      or u.company_id == admin.company_id]
    payload = []
    for u in users:
        try:
            st = basecamp_provisioning.status_for_user(u)
        except Exception as e:
            st = {"error": str(e), "state": "error"}
        payload.append({
            "user_id": u.user_id,
            "email": u.email,
            "display_name": u.display_name,
            "company_id": u.company_id,
            "status": st,
        })
    return JSONResponse({"ok": True, "count": len(payload), "rows": payload})


@router.post("/users/{user_id}/bc-ai-provision")
async def ai_clone_provision(request: Request, user_id: str,
                                                          ai_email: str = Form(""),
                                                          ai_display_name: str = Form("")):
    """Programmatically invite the user's "<Name> AI" Basecamp account
    via the BC API. After this returns, the user still has to accept
    the email invite + Incognito-reconnect via /profile/connect-basecamp;
    that's BC's design, not ours.
    """
    admin = _require_admin(request)
    u = tenancy.get_user(user_id)
    if not u:
        raise HTTPException(404, "user not found")
    if admin.company_id != "colaberry" and u.company_id != admin.company_id:
        raise HTTPException(403, "can only modify users in your own company")

    from execution.products.library import basecamp_provisioning
    result = basecamp_provisioning.provision_bc_ai_account(
        u, ai_email=ai_email.strip(), ai_display_name=ai_display_name.strip(),
    )
    if result.ok:
        _audit(admin.user_id, "bc_ai.provisioned_via_api",
                      target=user_id,
                      notes=(f"bc_user_id={result.bc_user_id} "
                                  f"email={result.bc_user_email} "
                                  f"project_id={result.project_id}"))
        msg = (f"Invited {result.bc_user_email} (BC user id "
                      f"{result.bc_user_id}). The user must now accept the "
                      f"invite email + Incognito-reconnect.")
        return RedirectResponse(
            url=f"/admin/users/{user_id}/ai-clone?provision_msg={urllib.parse.quote(msg)}",
            status_code=303,
        )
    _audit(admin.user_id, "bc_ai.provision_failed", target=user_id,
                  notes=f"{result.error_code}: {result.error_detail[:140]}")
    err = f"{result.error_code}: {result.error_detail}"
    return RedirectResponse(
        url=f"/admin/users/{user_id}/ai-clone?provision_error={urllib.parse.quote(err)}",
        status_code=303,
    )


@router.post("/users/{user_id}/ai-clone")
async def ai_clone_save(request: Request, user_id: str,
                        bc_user_id: int = Form(...),
                        bc_ai_clone_name: str = Form(...),
                        bc_ai_clone_token: str = Form(...),
                        bc_extra_buckets: str = Form(""),
                        ttl_days: Optional[int] = Form(14)):
    admin = _require_admin(request)
    u = tenancy.get_user(user_id)
    if not u:
        raise HTTPException(404, "user not found")
    if admin.company_id != "colaberry" and u.company_id != admin.company_id:
        raise HTTPException(403, "can only modify users in your own company")
    # Parse extra bucket ids (comma- or whitespace-separated)
    parsed_buckets: list[int] = []
    for chunk in bc_extra_buckets.replace(",", " ").split():
        try:
            parsed_buckets.append(int(chunk.strip()))
        except ValueError:
            continue
    # 1. Update User record with the human's BC id + clone display name + extra buckets
    u.bc_user_id = int(bc_user_id)
    u.bc_ai_clone_name = bc_ai_clone_name.strip() or f"{u.display_name} Clone"
    u.bc_extra_buckets = parsed_buckets
    tenancy.upsert_user(u)
    # 2. Grant scope + store the token in the vault
    if "basecamp_ai_clone" not in tenancy.current_scopes(u.user_id):
        tenancy.grant_scope(u.user_id, "basecamp_ai_clone", granted_by_user_id=admin.user_id)
    vault.store_secret(
        u.user_id, "basecamp_ai_clone", bc_ai_clone_token.strip(),
        caller_id=admin.user_id, ttl_days=ttl_days,
        notes=f"AI clone {u.bc_ai_clone_name} (BC user id {bc_user_id})",
    )
    _audit(admin.user_id, "ai_clone.set",
           target=user_id, notes=f"bc_user_id={bc_user_id} clone={u.bc_ai_clone_name}")
    return RedirectResponse(url=f"/admin/users/{user_id}?ai_clone_saved=1", status_code=303)
