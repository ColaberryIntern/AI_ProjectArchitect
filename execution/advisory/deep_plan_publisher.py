"""Publish a story-driven deep plan (from ``deep_plan``) to Basecamp.

One to-do list; each **release** a group; each **story** a rich, assigned, due-
dated to-do (narrative / fulfills-REQ / owner-agent / Gherkin acceptance / build /
vibe / trust + links to the project docs). Plus the four documents (Requirements,
Architecture/Agent map, Build Guide, Traceability Matrix) into Docs & Files.

Reuses the operator-scoped Basecamp primitives in ``project_plan_reconciler`` /
``basecamp_build_writer`` so posts are authored as the operator, every to-do is
**assigned** to them and **due-dated** across the release's program weeks (My Day
drops todos that have neither a due date nor recent activity).
"""
from __future__ import annotations

import html
import logging
import re
from datetime import timedelta

logger = logging.getLogger(__name__)

# Program calendar: a 9-week build occupies weeks 3–11 (week 12 = presentations).
_ANCHOR_OFFSET_DAYS = 14  # cohort-start Monday → Monday of week 3


def anchor_from_cohort_start(cohort_start_monday):
    """The first due-date Monday (start of program week 3)."""
    return cohort_start_monday + timedelta(days=_ANCHOR_OFFSET_DAYS)


def _weekdays(a, b):
    d, out = a, []
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _due_dates(n, ws, we, anchor):
    """Spread ``n`` due dates across weekdays of program weeks ``ws``..``we``."""
    start = anchor + timedelta(days=(ws - 3) * 7)
    end = anchor + timedelta(days=(we - 3) * 7 + 4)   # Friday of the last week
    days = _weekdays(start, end)
    if not days or n <= 0:
        return [None] * max(n, 0)
    return [days[min(len(days) - 1, round(i * (len(days) - 1) / max(1, n - 1)))].isoformat() for i in range(n)]


def _esc(s):
    return html.escape(str(s or ""))


def _gherkin_html(acc):
    """Render acceptance scenarios as a Gherkin list; trust scenarios get a shield."""
    items = []
    for a in acc or []:
        shield = "🛡 " if a.get("trust") else ""
        clauses = []
        if a.get("given"):
            clauses.append(f"<em>Given</em> {_esc(a['given'])}")
        if a.get("when"):
            clauses.append(f"<em>When</em> {_esc(a['when'])}")
        clauses.append(f"<em>Then</em> {_esc(a.get('then'))}")
        items.append(f"<li>{shield}<strong>{_esc(a.get('scenario'))}</strong> — " + "; ".join(clauses) + "</li>")
    return f"<ul>{''.join(items)}</ul>" if items else ""


def _story_html(s, doc_links_html=""):
    fulfills = " ".join(f"<code>{_esc(f)}</code>" for f in (s.get("fulfills") or []))
    rows = [
        f"<div><strong>Story:</strong> {_esc(s.get('narrative'))}</div>",
        f"<div><strong>Fulfills:</strong> {fulfills or '—'} &nbsp;·&nbsp; <strong>Owner agent:</strong> {_esc(s.get('owner_agent') or '—')}</div>",
    ]
    if s.get("slice"):
        rows.append(f"<div><strong>Slice:</strong> <code>{_esc(s.get('slice'))}</code></div>")
    rows.append("<div><strong>Acceptance (Gherkin = demo script + loop stop):</strong></div>")
    rows.append(_gherkin_html(s.get("acceptance")))
    if s.get("build"):
        rows.append(f"<div><strong>Build:</strong> {_esc(s.get('build'))}</div>")
    if s.get("vibe"):
        rows.append(f"<div><strong>Vibe-code it:</strong> <em>{_esc(s.get('vibe'))}</em></div>")
    if s.get("trust"):
        rows.append(f"<div><strong>Trust (TBI):</strong> {_esc(s.get('trust'))} &nbsp; <strong>Loop stop:</strong> all acceptance scenarios pass.</div>")
    if doc_links_html:
        rows.append(doc_links_html)
    return "".join(rows)


def _md_html(md):
    out = []
    for line in (md or "").split("\n"):
        l = line.rstrip()
        if not l:
            out.append("<br>"); continue
        m = re.match(r"(#{1,4})\s+(.*)", l)
        if m:
            lvl = min(len(m.group(1)), 3)
            out.append(f"<h{lvl}>{_esc(m.group(2))}</h{lvl}>"); continue
        if l.lstrip().startswith(("- ", "* ")):
            out.append(f"<li>{_esc(l.lstrip()[2:])}</li>"); continue
        body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", _esc(l))
        out.append(f"<div>{body}</div>")
    return "".join(out)


def _publish_docs(plan, user, bc_project_id, name):
    """Create the four documents in the project vault. Returns [(title, url), ...]."""
    from execution.products.library import mcp_tools
    out = []
    try:
        acct = mcp_tools._bc_account()
        proj = mcp_tools._bc_request("GET", f"https://3.basecampapi.com/{acct}/projects/{bc_project_id}.json", user=user)
        vault = next((d.get("id") for d in proj.get("dock", []) if d.get("name") == "vault"), None)
        if not vault:
            return out
        for title, key in (("Requirements", "requirements"), ("Architecture & Agent Map", "architecture"),
                            ("Build Guide", "build_guide"), ("Traceability Matrix", "rtm")):
            md = plan.get(key)
            if not md:
                continue
            r = mcp_tools._bc_request(
                "POST", f"https://3.basecampapi.com/{acct}/buckets/{bc_project_id}/vaults/{vault}/documents.json",
                payload={"title": f"{name} - {title}", "content": _md_html(md), "status": "active"}, user=user)
            if isinstance(r, dict):
                url = r.get("app_url") or f"https://app.basecamp.com/{acct}/buckets/{bc_project_id}/documents/{r.get('id')}"
                out.append((f"{name} - {title}", url))
    except Exception as e:  # docs are non-blocking — the stories are the build
        logger.warning(f"[deep_plan] document publish failed (non-blocking): {e}")
    return out


def publish_deep_plan(plan, user, bc_project_id, anchor_monday, list_name, project_name=""):
    """Create the BC todolist + release groups + assigned/due-dated story todos,
    plus the four documents. Returns {todolist_id, created, docs, doc_urls}.
    """
    from execution.advisory import basecamp_build_writer as bw
    from execution.advisory import project_plan_reconciler as rec

    name = project_name or plan.get("project", "Project")
    assignee = bw.resolve_operator_bc_person_id(user, bc_project_id)
    if not assignee:
        raise RuntimeError(f"could not resolve operator BC person id in {bc_project_id}; every task must be assigned")
    todoset = rec._discover_todoset(user, bc_project_id)
    overview = (f"{name} - story-driven build plan. {plan.get('story_count', 0)} traceable user stories across "
                f"{len(plan.get('releases', []))} releases (walking-skeleton-first). Requirements / Architecture / "
                f"Build Guide / Traceability Matrix are in Docs & Files.")
    list_id = rec._create_todolist(user, bc_project_id, todoset, list_name, overview)

    # Docs first, so each story can link them.
    doc_urls = _publish_docs(plan, user, bc_project_id, name)
    doc_links_html = ""
    if doc_urls:
        links = "".join(f'<li><a href="{u}">{_esc(t)}</a></li>' for t, u in doc_urls)
        doc_links_html = f"<div><strong>📎 Project documents:</strong></div><ul>{links}</ul>"

    story_index = {s["id"]: s for s in plan.get("stories", [])}
    created = 0
    for rel in plan.get("releases", []):
        ws, we = rel.get("weeks", (3, 3))
        wlabel = f" (wk {ws}" + (f"-{we}" if we != ws else "") + ")"
        gid = rec._create_group(user, bc_project_id, list_id, f"{rel.get('key', '').upper()} - {rel.get('name', '')}{wlabel}")
        sids = [sid for sid in rel.get("stories", []) if sid in story_index]
        for sid, due in zip(sids, _due_dates(len(sids), ws, we, anchor_monday)):
            s = story_index[sid]
            owner = f"  [{s['owner_agent']}]" if s.get("owner_agent") else ""
            content = (f"{sid} - {s.get('title', '')}{owner}")[:230]
            rec._create_todo(user, bc_project_id, gid, content, _story_html(s, doc_links_html),
                             [assignee] if assignee else [], due)
            created += 1

    return {"todolist_id": list_id, "created": created, "docs": [u for _, u in doc_urls], "doc_urls": doc_urls}
