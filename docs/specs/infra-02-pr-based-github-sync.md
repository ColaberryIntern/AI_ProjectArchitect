# [Infra 2] PR-based GitHub sync + CI smoke gate

**Ticket:** Basecamp [9953889154](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889154) · due 2026-06-23
**Status:** Shipped
**Depends on:** [Infra 1] ✅ (direct-commit sync foundation), [Workflow 1] ✅ (approval trigger)

---

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | GitHub App with write access to ColaberryIntern/AI_ProjectArchitect | Reuses [Infra 1]'s `gh` CLI auth (cached on prod box); no new app required. Token scope: `repo` |
| 2 | Webhook on library "Colaberry approved" toggle | `maybe_trigger_pr_for_approval()` hook — wireable into `Workflow 1::decide_review` for fire-on-approve |
| 3 | Renderer that turns library record into clean Markdown with frontmatter | Reuses `github_sync.render_asset_markdown()` (unchanged) |
| 4 | Branch + PR strategy: auto-merge or human review | Per-config `pr_auto_merge` flag in `library_approvers.json`. Default = `false` (safer: every sync gets human eyes). When `true`, gh's `--auto --squash` waits for required checks |
| 5 | CI gate: lint + smoke-test that artifact parses | `.github/workflows/library-sync-check.yml` runs `scripts/library_sync_smoke.py` on every PR touching `library/**/*.md` |

## Architecture

```
[Workflow 1 approve] ──(optional hook)──> open_sync_pr()
                                              │
                                              ├─ gh api git/ref/heads/main   (capture base SHA)
                                              ├─ gh api git/refs POST         (create sync branch)
                                              ├─ gh api contents PUT/DELETE   (write file on branch)
                                              ├─ gh pr create                 (open PR with smoke-test ref)
                                              └─ (if pr_auto_merge) gh pr merge --auto --squash

                            PR opens, GitHub Actions fires:
                            .github/workflows/library-sync-check.yml
                                              │
                                              └─ python scripts/library_sync_smoke.py changed/*.md
                                                  ├─ valid UTF-8
                                                  ├─ YAML frontmatter (title, kind, slug, version, owner)
                                                  ├─ non-empty body
                                                  └─ no secret patterns (ghp_, sk-, AKIA, BEARER, PEM keys)

                            Green CI + auto-merge → merge to main
                            Green CI + human approval → manual merge
                            Red CI → PR blocked, audit-logged
```

## Files shipped

| File | Purpose |
|---|---|
| `execution/products/library/github_pr_sync.py` | NEW — `open_sync_pr`, `reconcile_via_prs`, `maybe_trigger_pr_for_approval`, `PRResult` dataclass |
| `scripts/library_sync_smoke.py` | NEW — CI gate: frontmatter validation + secret scan |
| `.github/workflows/library-sync-check.yml` | NEW — runs smoke test on PRs touching `library/**/*.md` |
| `tests/execution/products/test_github_pr_sync.py` | NEW — 15 tests |

## Subprocess discipline

`_run()` is a thin `subprocess.run` wrapper kept narrow so tests can monkeypatch it. The module makes **zero** subprocess calls at import time — safe to import anywhere.

## Auto-merge default

`pr_auto_merge: false` in `config/library_approvers.json` is the v1 default. Rationale: until the smoke test has been observed catching ≥1 real failure in prod, every sync gets human review. To enable later:

```json
{
  "approvers": [...],
  "approval_target_repo": "ColaberryIntern/AI_ProjectArchitect",
  "pr_auto_merge": true
}
```

When true, `gh pr merge --auto --squash` is invoked — GitHub waits for the required `Library sync smoke` check before merging.

## Tests (15/15)

- `open_sync_pr`: creates branch + file PUT + PR; rejects unauthorised approver; dry_run skips gh; audits failures; auto-merge invocation when configured; clear error when `gh` not available
- `maybe_trigger_pr_for_approval`: returns None for non-Colaberry tenant; respects `LIBRARY_PR_SYNC_DISABLED=1` env
- Smoke test CLI: passes valid artifact; blocks missing frontmatter; blocks missing required keys; detects GitHub PAT; detects AWS key; blocks empty body; skips non-markdown files

## Activation steps for Ali

1. **Merge PR #1** (the multitenant-os feature branch with this code)
2. Confirm `gh auth status` on the prod box has `repo` scope (already true per Infra 1 install)
3. To turn on automatic PR-on-approve: uncomment the hook call in `tenancy.decide_review` (currently the module exposes `maybe_trigger_pr_for_approval` but the call site is documented, not wired — keeps Workflow 1 deterministic)
4. To enable auto-merge: flip `pr_auto_merge: true` in `config/library_approvers.json` after observing a few real PRs work end-to-end

## Trade-offs / deferred

- **No webhook listener** — the trigger is internal (Workflow 1 hook), not an inbound GitHub webhook. If/when the library is hosted somewhere that needs an external trigger surface (e.g. a Slack `/approve` command), add a router endpoint that calls `open_sync_pr()`.
- **Per-tenant target repos** — current sync only knows about `ColaberryIntern/AI_ProjectArchitect`. Each customer tenant publishing to their own repo is the [Provision 4] follow-up (not yet ticketed).
- **Single-file PRs** — every approval opens its own PR. A batching mode ("one PR for all today's approvals") could halve review overhead but loses per-asset audit clarity. Deferred until volume justifies.
- **Conflict resolution** — if two operators approve concurrently, both PRs will open against `main`. The second PR's merge will rebase or conflict; gh handles the common case. Rare deep conflicts get surfaced in the audit.
