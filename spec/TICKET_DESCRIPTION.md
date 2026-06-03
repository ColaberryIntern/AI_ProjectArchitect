# [00 START HERE] Build Index — read this first to build the entire system

Source: https://app.basecamp.com/3945211/buckets/7463955/todos/9956775973

Status: active | Due: 2026-06-08 | Assignees: CB System

---

Single source of truth for the advisor.colaberry.ai buildout. The full plan (mission, architecture, all 33 tickets with dependency-ordered build sequence, repo conventions, open questions with defaults) is in the comment below. A Claude Code agent in the AI_ProjectArchitect repo should be able to read this list cold and build the system end to end. The list is the contract. The repo is the canvas.
**Build top-down:**

- **Week 1 (foundation):** Infra 1 → Auth 1 → Auth 2 + Provision 2

- **Week 2 (admin + provisioning):** Admin 1 → Admin 3 → Provision 1 → Admin 2

- **Week 3 (data + library UX):** Data 1 → Library 1 + Library 2

- **Week 4 (workflows + sync):** Workflow 1 → Workflow 2 + Infra 2

- **Week 5 (deploy + docs):** Deploy 1 → Infra 3 (refocused)

- **Parallel:** Karun + Kes pilots (Week 1-4) → Phase 2 (Day 31-60) → Phase 3 (Day 61-90) → Day 90 retro + Strategic eval