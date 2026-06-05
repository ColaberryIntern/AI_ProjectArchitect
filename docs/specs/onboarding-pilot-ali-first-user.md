# [Onboarding pilot] Ali as user #1 end-to-end test

**Ticket:** Basecamp [9967849730](https://app.basecamp.com/3945211/buckets/7463955/todos/9967849730)
**Status:** Plan drafted; awaiting Ali's review + decisions on open questions
**Depends on:** Op 1-5 (just shipped ✅), PR #1 multitenant-os foundation (open, deployment status TBD)

---

## Goal

Walk Ali through the complete onboarding flow as the first real user. This is a **dogfood test** of the per-operator experience layer (Op 1-5, shipped 2026-06-05) integrating with the multi-tenant foundation (PR #1: Auth, Admin, Provision, Library, Workflow).

Two outcomes, both required:

1. **Working end-to-end flow** — Ali experiences every step from "click the invite email" to "create a skill that lands in the library" without hand-holding from outside the system.
2. **Honest gap inventory** — every step that breaks or requires manual workaround gets logged + ticketed so the second user (Karun, Kes, or an intern) has a smooth experience.

Ali plays **two roles** in the test: admin Ali (provisioning) and user Ali (onboarding). This is fine for a dogfood test — the two roles will be distinct people in production.

---

## The 18-step end-to-end flow (the experience Ali wants)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase A — Admin setup (Ali wearing admin hat)                               │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1.  Admin opens admin console                                               │
│ 2.  Admin clicks "Add user" → fills form (email, display_name, tenant)      │
│ 3.  Admin selects tools-access scope (Gmail, BC, GitHub, etc.)              │
│ 4.  Admin clicks "Provision GitHub workspace" → workspace repo created      │
│ 5.  Admin pastes credential tokens into vault                               │
│ 6.  Admin clicks "Provision personal BC project" → BC project created       │
│ 7.  Admin clicks "Send invitation email" → email goes out                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                       ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase B — User onboarding (Ali wearing user hat)                            │
├─────────────────────────────────────────────────────────────────────────────┤
│ 8.  Email arrives in inbox                                                  │
│ 9.  User clicks "Sign in" → Google SSO → JWT minted → portal lands          │
│ 10. User sees portal dashboard with their tools + workspace links           │
│ 11. User clicks "Open my workspace" → GitHub repo                           │
│ 12. User clones the repo locally                                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                       ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase C — First Claude Code session                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ 13. User runs `claude` in the cloned workspace                              │
│ 14. Session-start surfaces the 5-layer assembled context                    │
│ 15. User makes a substantive request → Claude prompts for ticket creation   │
│ 16. User confirms → BC ticket created in personal project                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                       ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase D — Skill creation + library sync                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ 17. User creates a skill in `.claude/skills/` (the "personal folder" skill) │
│ 18. Skill auto-syncs to library → approval queue → published                │
│ 19. Op 4 auto-closes the session ticket on session-end                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Gap analysis per step

For each step: **what exists**, **what's missing**, **dependency**, **effort to close gap**.

| # | Step | Exists | Gap | Dependency | Effort |
|---|---|---|---|---|---|
| 1 | Admin console UI | `docs/specs/admin-01-admin-console.md` (shipped on feature/multitenant-os branch) | Branch not merged to main; portal-side UI not deployed at advisor.colaberry.ai (status to be verified) | PR #1 merge OR feature-branch deploy | 2-4 hrs (deploy verification + smoke test) |
| 2 | Add-user form | Admin 3 (`admin-03-add-new-company.md`) on feature branch | Same as Step 1 | PR #1 merge | (folded into Step 1) |
| 3 | Tools-access matrix UI | Admin 2 (`admin-02-tools-access-matrix.md`) on feature branch | Same as Step 1 | PR #1 merge | (folded into Step 1) |
| 4 | GitHub workspace repo provision | `execution/products/library/workspaces.py` on feature branch (per WORK_LOG.md) | (a) `workspaces.py` not on main. (b) Template repo `ColaberryIntern/workspace-template` not yet created. (c) `seed_workspace()` from Op 1 needs to be wired into `provision_user_workspace()`. (d) `GITHUB_ADMIN_TOKEN` env var must be set in prod. | PR #1 merge + Op 1 integration | 4-6 hrs |
| 5 | Credentials vault | Provision 2 (`provision-02-credentials-vault.md`) on feature branch. Op 1 references `${{ vault.X }}` placeholders. | (a) Not on main. (b) `LIBRARY_VAULT_MASTER_KEY` env var must be set in prod. (c) MCP runtime vault-decrypt hook unclear — see Provision 2 spec § Hand-off. | PR #1 merge | 4-8 hrs |
| 6 | Personal BC project provision | `execution/products/library/personal_bc_provisioner.py` (Op 2, on main ✅) | (a) Auto-grant user as BC collaborator NOT implemented (v01 says "admin manually adds from BC UI"). (b) BC token in env at provision time. | None (can run today) | 1 hr to wire admin button + 2 hrs for v02 auto-grant |
| 7 | Invitation email | None | Script `sendOnboardingInviteEmail.js` does not exist. Template + Mandrill send + magic-link generation all need to be written. | Step 9 (sign-in link target must exist) | 2-3 hrs |
| 8 | Email arrives | Inherent in Step 7 | None | None | 0 |
| 9 | Google SSO sign-in | Auth 2 (`auth-02-google-sso.md`) on feature branch | (a) Not deployed. (b) `GOOGLE_OAUTH_CLIENT_ID` + `_SECRET` + `_REDIRECT_URI` + `LIBRARY_SESSION_SECRET` env vars must be set. (c) Google Cloud OAuth app needs OAuth consent screen configured. | PR #1 merge + Google Cloud config | 3-4 hrs (mostly Google Cloud setup) |
| 10 | Portal dashboard | Various dashboard components on feature branch (Library 1+2 identity badge + scope switcher) | Same deployment question as Steps 1-3 | PR #1 merge | (folded into Step 1) |
| 11 | "Open workspace" link → GitHub | Inherent if Step 4 succeeded | None | Step 4 working | 0 |
| 12 | Clone locally | Standard git | None | Step 4 working | 0 |
| 13 | Run `claude` in workspace | Claude Code CLI (user-installed) | None | None | 0 |
| 14 | Session-start: 5-layer assembled context | Op 1 `operator_scaffold.assemble_context()` on main ✅ | **HOW DOES CLAUDE CODE ACTUALLY INVOKE THIS?** Claude Code's CLAUDE.md cascade reads files at session start, but it doesn't natively run a Python session-start hook. Three options: (A) wrapper script `claude-onboard` that runs the assembler then launches `claude`. (B) `.claude/settings.json` `hooks` entry that runs at session start (need to verify Claude Code supports this on `SessionStart` event). (C) `claude` reads a static concatenated file produced by a cron/scheduled job. **Decision needed.** | Op 1 module exists; integration mechanism undecided | 2-4 hrs depending on path |
| 15 | Prompt classification → ticket creation | Op 2 `ticket_creation_flow.classify_prompt()` on main ✅ | Same hook integration question as Step 14. Claude must call the classifier on the user's first prompt and act on the result. | Decided alongside Step 14 | (folded) |
| 16 | BC ticket created in personal project | Op 2 `create_ticket_for_session()` exists ✅ | (a) Needs `BASECAMP_ACCESS_TOKEN` available to the user's local env. (b) Needs the user's `personal_bc_project_id` known to the local Claude session (read from `users.personal_bc_project_id` somehow — probably via the portal API). | Step 9 working (so user can fetch their project_id) | 1-2 hrs |
| 17 | Skill creation in `.claude/skills/` | Claude Code natively supports custom skills via files in this directory | (a) No "create skill" wizard — user writes the file directly. (b) Naming convention + frontmatter format must be documented in the seeded `.claude/README.md`. | Op 1 scaffold + minor doc edit | 1 hr |
| 18 | Library auto-sync (skill → PR → approve → published) | Infra 1 (`infra-01-...`) auto-sync + Infra 2 (`infra-02-pr-based-github-sync.md`) + Workflow 1 (publish queue) + Library 1 (approval filter) — all on feature branch | (a) Not deployed. (b) Auto-sync trigger when files appear in `.claude/skills/` of a user workspace repo needs explicit wiring (workspaces are individual repos; the sync watches the central library repo). (c) Approval routing to admin needs UI. | PR #1 merge + sync trigger wiring | 4-6 hrs |
| 19 | Op 4 auto-close on session-end | `execution/products/library/auto_close.py` on main ✅ | Same hook integration question as Step 14 (when does the "session-end" event fire?). | Decided alongside Step 14 | (folded) |

---

## The big gaps in plain language

1. **PR #1 deployment status is the load-bearing unknown.** Steps 1-5, 9, 10, 18 all depend on the multi-tenant foundation being deployed somewhere reachable. Per WORK_LOG.md, PR #1 was open but never merged to main as of 2026-06-03. The 2 days of subsequent work (my_day reports + this Op 0-5 kickoff) all landed on main alongside but didn't change the merge state. **Action needed:** verify PR #1 current state; decide merge vs deploy-from-branch.

2. **Claude Code session-start hook mechanism is undecided.** Op 1's assemble_context() exists and is correct, but how Claude Code actually invokes it at session start is an integration question I deferred during the kickoff. Three options exist (wrapper script / .claude/settings.json hook / pre-computed file). **Action needed:** Ali picks the integration mechanism. Recommend Option B (`.claude/settings.json` hook on `SessionStart` event) if Claude Code supports it — cleanest user experience.

3. **Invitation email script doesn't exist.** Has to be written: Mandrill send template + magic-link generation (or Google SSO redirect URL) + content covering what the user got provisioned with. **Action needed:** write the script. ~2-3 hours.

4. **Op 1 seed_workspace() + Provision 1 workspaces.py integration not wired.** Op 1's seed_workspace() is designed to be called from workspaces.provision_user_workspace(). The wiring is a one-line addition but workspaces.py isn't on main yet, so the wiring can't happen until PR #1 merges. **Action needed:** when PR #1 merges, add the call.

5. **Library auto-sync trigger from a user's workspace repo doesn't exist.** Infra 2 has PR-based sync from the central library repo. But the flow "user creates `.claude/skills/foo.md` in their workspace repo → PR opens against the central library" requires watching workspace repos for new skills + opening cross-repo PRs. **Action needed:** scope this as a follow-up; Library 3 ticket. Estimated 4-6 hours.

---

## Pre-flight prerequisites (must do before the pilot runs)

| # | Prerequisite | Owner | Notes |
|---|---|---|---|
| P1 | Verify PR #1 current state + decide deployment path | Ali | Merge to main, OR deploy from feature/multitenant-os branch, OR something else. This is the biggest single decision. |
| P2 | Set env vars on the target deployment | Ali | `GOOGLE_OAUTH_CLIENT_ID`, `_SECRET`, `_REDIRECT_URI`, `LIBRARY_SESSION_SECRET`, `LIBRARY_VAULT_MASTER_KEY`, `GITHUB_ADMIN_TOKEN`, `GITHUB_LIBRARY_REPO`, `BASECAMP_ACCESS_TOKEN`. Full list in `docs/specs/deploy-01-multitenant-cutover.md` (shipped). |
| P3 | Create `ColaberryIntern/workspace-template` GitHub repo | Ali | Per Provision 1 spec § Activation steps. Seed with `.claude/skills/`, `.mcp.json` (empty), `README.md`, `USER_PROFILE.md`. Mark as template repo via `gh repo edit --template`. |
| P4 | Decide Claude Code session-start hook mechanism | Ali (decision) | Picks A/B/C from gap #2 above. Recommend B if available. |
| P5 | Write invitation email script | Claude (next session) | `backend/src/scripts/sendOnboardingInviteEmail.js`. Follow existing Mandrill template. |
| P6 | Wire seed_workspace() into provision_user_workspace() | Claude (next session, after PR #1 merge) | One-line integration. |
| P7 | Decide what skill Ali creates for the test | Ali | E.g. "personal-folder-organizer" or something specific. Affects the Phase D verification. |

---

## Proposed execution roadmap (5 phases of work)

Each phase = one or more BC tickets in the existing rollout list (or a new list). Build order is the natural dependency chain.

### Phase 1 — Foundation deploy (1-2 days)

**Goal:** PR #1 multi-tenant foundation reachable somewhere Ali can hit it.

- Ticket 1.1: Verify PR #1 state + decide merge/deploy path
- Ticket 1.2: Set all env vars per `deploy-01-multitenant-cutover.md`
- Ticket 1.3: Deploy + smoke test admin console URL
- Ticket 1.4: Smoke test Google SSO sign-in (Ali signs in once successfully)

**Pilot test gates this phase opens:** Steps 1-3, 9, 10.

### Phase 2 — Workspace provisioning (~1 day)

**Goal:** When admin clicks "provision workspace," all the right artifacts get created.

- Ticket 2.1: Create `ColaberryIntern/workspace-template` GitHub repo
- Ticket 2.2: Wire Op 1's `seed_workspace()` into Provision 1's `workspaces.provision_user_workspace()`
- Ticket 2.3: Provision 2 vault smoke test (paste a token, verify decrypt at runtime)
- Ticket 2.4: Wire Op 2's `personal_bc_provisioner` into the admin "add user" flow

**Pilot test gates this phase opens:** Steps 4-6.

### Phase 3 — Invitation + session-start (~1 day)

**Goal:** User receives a clean email, clicks through, and Claude Code reads the assembled context on first run.

- Ticket 3.1: Write `sendOnboardingInviteEmail.js` (Mandrill send + content + sign-in CTA)
- Ticket 3.2: Implement the Claude Code session-start hook (whichever mechanism Ali picks in P4)
- Ticket 3.3: Wire Op 2's `ticket_creation_flow` into the session-start path so first substantive prompt triggers ticket creation

**Pilot test gates this phase opens:** Steps 7-16.

### Phase 4 — Skill creation + library sync (~1 day)

**Goal:** User creates a skill, it lands in the central library.

- Ticket 4.1: Document the skill-file format in seeded `.claude/README.md` (Op 1 extension)
- Ticket 4.2: Build the workspace-repo → library-repo sync trigger (the missing piece; possibly as `Library 3`)
- Ticket 4.3: Test the approval queue end-to-end (admin sees pending skill, approves, it publishes)

**Pilot test gates this phase opens:** Steps 17-18.

### Phase 5 — The actual pilot run (~half day)

**Goal:** Ali walks through all 18 steps end-to-end with full verification.

- Ticket 5.1: Pre-flight checklist run (all P1-P7 done)
- Ticket 5.2: Run the dogfood test script (see below)
- Ticket 5.3: Log every break + gap + workaround
- Ticket 5.4: Open follow-up tickets for the second-user smoothness work

**Pilot test gates this phase opens:** the actual pilot run.

**Total estimated effort: 4-5 days of focused work.** Roughly 1 day per phase with overlap possible on Phase 2 + 3.

---

## The dogfood test script (Phase 5)

Once Phases 1-4 are done, Ali sits down for ~2 hours with this script. Each step has a verification checkpoint.

### Pre-flight (Ali, 10 min)

- [ ] Confirm prod env vars set (P2)
- [ ] Confirm `ColaberryIntern/workspace-template` repo exists (P3)
- [ ] Confirm the admin console URL loads (cold cache, no cookies)
- [ ] Confirm Ali's `ali@colaberry.com` is NOT already a user in the tenancy table (clean slate)

### Phase A — Admin setup (Ali, 20 min)

- [ ] Step 1: Open admin console at `<URL TBD>`. Verify navigation loads.
- [ ] Step 2: Click "Add user". Fill form with email=`ali@colaberry.com`, display_name=`Ali Muwwakkil`, tenant=`colaberry`. Submit. **Verify:** new row in tenancy table.
- [ ] Step 3: On the user detail page, toggle on Gmail / Calendar / Basecamp / CCPP / GitHub / Mandrill. Submit. **Verify:** AccessScope rows exist.
- [ ] Step 4: Click "Provision GitHub workspace". **Verify:** `ColaberryIntern/ali-workspace` exists on GitHub; Ali is added as collaborator; the repo contains CLAUDE.md, PROGRESS.md, OPERATOR_MEMORY.md, .claude/ scaffolding (proves seed_workspace() ran).
- [ ] Step 5: On the vault tab, paste tokens for each granted tool. Save. **Verify:** vault audit log has `credential.set` entries.
- [ ] Step 6: Click "Provision personal BC project". **Verify:** BC project `Ali Muwwakkil Personal` exists in account 3945211; Ali's `users.personal_bc_project_id` is populated.
- [ ] Step 7: Click "Send invitation email". **Verify:** Mandrill returns a message-id; the email is queued.

### Phase B — User onboarding (Ali, 15 min, fresh browser session)

- [ ] Step 8: Open ali@colaberry.com inbox. **Verify:** invitation email lands within 60s. Subject is clear and welcoming.
- [ ] Step 9: Click "Sign in" in the email. **Verify:** Google OAuth flow runs; redirects to portal dashboard; JWT cookie set.
- [ ] Step 10: On the dashboard, **verify:** Ali's tools listed, workspace repo link present, personal BC project link present.
- [ ] Step 11: Click "Open my workspace" → opens `https://github.com/ColaberryIntern/ali-workspace` in new tab. **Verify:** repo loads; Ali sees the seeded files.
- [ ] Step 12: Clone locally: `git clone https://github.com/ColaberryIntern/ali-workspace.git ~/ali-workspace && cd ~/ali-workspace`. **Verify:** files appear locally.

### Phase C — First Claude Code session (Ali, 20 min)

- [ ] Step 13: Run `claude` in `~/ali-workspace`. **Verify:** Claude Code starts; session-start hook (whichever mechanism was chosen) fires.
- [ ] Step 14: **Verify:** the 5-layer assembled context surfaces. Layer 1 (org doctrine), Layer 2 (3 colaberry.com sites scraped), Layer 4 (Ali's per-user CLAUDE.md). Layer 3 + 5 empty (no tenant policy, no learned memory yet). Op 1 working end-to-end.
- [ ] Step 15: Type a substantive request: e.g. "Build a skill that organizes files in a personal folder by type." **Verify:** Claude responds with "I'll create a Basecamp ticket — proposed title: ... Edit or confirm?" (Op 2 ticket_creation_flow working).
- [ ] Step 16: Reply `confirm`. **Verify:** new BC todo lands in `Ali Muwwakkil Personal` project; `.claude/session-state.json` written; Claude proceeds with the work.

### Phase D — Skill creation + library sync (Ali, 30 min)

- [ ] Step 17: Claude creates `.claude/skills/personal-folder-organizer.md` with the skill definition (frontmatter + body). **Verify:** Op 3 ticket_updater posts a `file_create` card on the BC ticket.
- [ ] Step 18: Claude commits the file + pushes to the workspace repo. **Verify:** auto-sync trigger fires → PR opens against the central library repo → admin (Ali wearing admin hat again) sees pending skill in the approval queue → approves → skill is now in the library and visible to other Colaberry operators.
- [ ] Step 19: Session ends. **Verify:** Op 4 auto_close fires; confidence card lands on the BC ticket (probably 0.92 — same as the kickoff specs); ticket auto-completes if confidence ≥ 0.85.

### Post-run capture (Ali + Claude, 30 min)

- [ ] Every break: log to a new BC ticket "second user smoothness" with steps to reproduce.
- [ ] Every manual workaround: log + recommend the auto-fix.
- [ ] PROGRESS.md entry with full session timeline + verification evidence.
- [ ] Final wrap-up email summarizing the pilot result.

---

## Teardown / reset (if anything breaks mid-test)

If a step blocks the test and we can't move forward, we reset to a clean slate:

1. **Tenancy reset:** SQL `DELETE FROM users WHERE email='ali@colaberry.com';` + cascading delete of AccessScope/SessionState/etc.
2. **GitHub workspace teardown:** `gh repo delete ColaberryIntern/ali-workspace --yes`.
3. **BC personal project teardown:** Trash the BC project via API or UI.
4. **Vault teardown:** Delete vault entries for `ali@colaberry.com` scope.
5. **Local workspace:** `rm -rf ~/ali-workspace`.

Reset is destructive but safe — Ali isn't a "real" user yet, so no production data at risk.

---

## Logging + capture

Per Op 3 doctrine, every step posts a structured card on the pilot BC ticket. Verification evidence (URLs, screenshots, command outputs) gets pasted into the card body. The PROGRESS.md entry captures the high-level arc.

For future operators, the pilot output becomes the **second-user onboarding playbook** at `docs/specs/onboarding-playbook.md` (separate ticket, post-pilot).

---

## Validation scope (what this pilot DOES and DOES NOT validate)

**Validates:**
- The 5-layer context assembler works against real production URLs (Op 1).
- The mandatory-ticket doctrine fires correctly on a real user prompt (Op 2).
- Faithful BC updates post to a real ticket in a real personal BC project (Op 3).
- Auto-close fires correctly on session-end (Op 4).
- Operator memory file is seeded and the capture flow can be exercised (Op 5).
- The multi-tenant foundation (PR #1) actually onboards a user end-to-end.

**Does NOT validate:**
- Multi-user scenarios (this is single-user).
- Tenant isolation (Ali is in the `colaberry` tenant; no cross-tenant test).
- Production load (this is a single session).
- Cross-platform Claude Code support (Ali runs Windows; macOS/Linux deferred).
- Mobile / iPad workflow (out of scope).
- Anything dependent on the workspace-repo → library-repo sync if Phase 4 isn't built (skill creation might land in workspace only).

---

## Open questions for Ali (decisions needed before execution)

| # | Question | Recommended default |
|---|---|---|
| Q1 | PR #1 state: merge to main, or deploy from feature/multitenant-os branch? | **Merge to main.** Cleaner long-term; no perpetual feature-branch divergence. Risk: bigger blast if merge breaks something. Mitigation: deploy in stages per `deploy-01-multitenant-cutover.md` runbook. |
| Q2 | Where is the portal hosted — advisor.colaberry.ai or enterprise.colaberry.ai? | **Recommend `advisor.colaberry.ai`** (per recent memory: "/library/ is identity-gated via auth_gate middleware" on advisor.colaberry.ai). Already SSO-live in prod per the memory file. |
| Q3 | Claude Code session-start hook mechanism (A/B/C from Gap #2)? | **Option B** (.claude/settings.json hook on SessionStart event) if Claude Code supports it. Cleanest UX. Falls back to Option A (wrapper script) if not. |
| Q4 | What skill does Ali create for the test? | **"personal-folder-organizer"** per Ali's hint. Concrete enough to test the file-create path; benign enough that it doesn't matter if it lands in the library as a real published skill. |
| Q5 | Use Ali as both admin and user, or have a second admin (Karun? Dheeraj?) provision Ali? | **Ali as both.** Simpler. The two-admin scenario is a follow-up pilot. |
| Q6 | Run the pilot all in one sitting (~2 hours) or break across days? | **One sitting** if possible. Continuity beats fatigue here; the cross-session reset cost is real. |
| Q7 | After the pilot succeeds, who is user #2? | **Karun.** Already named in the Karun-1 / Karun-2 specs; this would replace the manual provisioning currently in those flows. |

---

## Hand-off

Once Ali approves this plan + answers the open questions:

1. **Next session creates the 5 phase tickets** as children of this pilot ticket (9967849730).
2. **Phase 1 starts:** verify PR #1 state, decide merge path, deploy.
3. **Each phase completes via the same Op 0-5 cycle:** PLAN → BUILD → TEST → REVIEW (email Ali) → ITERATE → SHIP → CLOSE.
4. **When all 5 phases close,** Ali runs the dogfood test script (Phase 5).
5. **Pilot success = the per-operator experience layer is production-ready.** Karun becomes user #2 the next week.

**Estimated end-to-end timeline: 5 working days from approval to pilot completion.**
