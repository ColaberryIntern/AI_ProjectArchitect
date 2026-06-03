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

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from execution.products.library import auth_google, tenancy, vault

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
