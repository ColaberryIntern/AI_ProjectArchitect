"""SessionStart hook for Claude Code in a Colaberry-provisioned workspace.

Per docs/specs/operator-00-kickoff.md Q3 + operator-01-per-user-scaffold.md
session-start protocol, this script runs when Claude Code starts a session in
a workspace seeded by Op 1's `seed_workspace()`. It:

  1. Reads the per-user CLAUDE.md + PROGRESS.md + OPERATOR_MEMORY.md from the
     workspace root.
  2. Fetches the org CLAUDE.md from the GitHub raw URL (1h TTL).
  3. Scrapes the 3 colaberry.com sites for the shared KB (24h TTL).
  4. Concatenates all 5 layers in priority order with banners.
  5. Prints the concatenated context to stdout. Claude Code consumes stdout
     output from the SessionStart hook as additional session context.

Wiring: lives in the per-user workspace at:
    .claude/session_start_hook.py  (this file, seeded by Op 1)
    .claude/settings.json          (also seeded — points to this script)

The settings.json entry looks like:
    {
      "hooks": {
        "SessionStart": [
          {"command": "python .claude/session_start_hook.py", "matcher": ".*"}
        ]
      }
    }

Stdlib only on the hook side. The script imports operator_scaffold IF available
(when the workspace user has cloned the central AI_ProjectArchitect repo as a
sibling), OR falls back to inline implementations.

Run standalone for testing:
    python -m execution.products.library.session_start_hook \
        --workspace-dir /path/to/workspace \
        --user-email karun@colaberry.com \
        --display-name "Karun Swaroop" \
        --tenant-id colaberry
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _load_operator_scaffold():
    """Try to import the central operator_scaffold module.

    If running inside a user's workspace repo (not the central repo), we won't
    have it as a Python import. Fall back to bundled implementations.
    """
    try:
        # Add the central repo path if it exists side-by-side
        for candidate in [
            Path.home() / "AI_ProjectArchitect",
            Path.home() / "code" / "AI_ProjectArchitect",
            Path("/opt/AI_ProjectArchitect"),
        ]:
            if (candidate / "execution" / "products" / "library" / "operator_scaffold.py").exists():
                sys.path.insert(0, str(candidate))
                break
        from execution.products.library import operator_scaffold  # type: ignore
        return operator_scaffold
    except ImportError:
        return None


def main():
    # Force UTF-8 on stdout so unicode in the assembled context (scraped from
    # colaberry.com) doesn't crash on Windows consoles using cp1252.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-dir", default=".",
                              help="Workspace root (default: cwd)")
    parser.add_argument("--user-email", default="",
                              help="Operator email; auto-detected from .claude/identity.txt if omitted")
    parser.add_argument("--display-name", default="",
                              help="Operator display name")
    parser.add_argument("--tenant-id", default="colaberry")
    args = parser.parse_args()

    workspace = Path(args.workspace_dir).resolve()

    # Auto-detect identity from a small identity file if not provided.
    # Op 1's seed_workspace writes .claude/identity.txt with email + display_name
    # so each session-start invocation doesn't need them as CLI args.
    identity_file = workspace / ".claude" / "identity.txt"
    user_email = args.user_email
    display_name = args.display_name
    personal_bc_project_id = ""
    personal_bc_todolist_id = ""
    basecamp_account_id = ""
    if identity_file.exists():
        for line in identity_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("email="):
                user_email = user_email or line.split("=", 1)[1]
            elif line.startswith("display_name="):
                display_name = display_name or line.split("=", 1)[1]
            elif line.startswith("personal_bc_project_id="):
                personal_bc_project_id = line.split("=", 1)[1].strip()
            elif line.startswith("personal_bc_todolist_id="):
                personal_bc_todolist_id = line.split("=", 1)[1].strip()
            elif line.startswith("basecamp_account_id="):
                basecamp_account_id = line.split("=", 1)[1].strip()

    if not user_email:
        # Defensive fallback — the operator is signed in somewhere, but we can't
        # personalize without an email. Print a minimal banner.
        print("# === Colaberry SessionStart hook ===")
        print("# (no operator identity available — set --user-email or seed .claude/identity.txt)")
        return 0

    scaffold = _load_operator_scaffold()
    if scaffold is None:
        # Bundled fallback: minimal banner pointing to the central CLAUDE.md
        print("# === Colaberry SessionStart hook (limited mode) ===")
        print(f"# Operator: {display_name or user_email}")
        print(f"# Tenant: {args.tenant_id}")
        print("# Org doctrine: https://raw.githubusercontent.com/ColaberryIntern/AI_ProjectArchitect/main/CLAUDE.md")
        print("# Shared KB: www.colaberry.com + www.colaberry.ai + www.enterprise.colaberry.com")
        print("# (Full 5-layer context unavailable — install AI_ProjectArchitect side-by-side for full assembly.)")
        print(_render_session_protocol(user_email, display_name,
                                                              personal_bc_project_id, basecamp_account_id,
                                                              personal_bc_todolist_id))
        return 0

    # Full mode: run the assembler
    org_fallback = workspace / "CLAUDE.md"  # last-resort fallback if remote fetch fails
    ctx = scaffold.assemble_context(
        user_email=user_email,
        user_display_name=display_name or user_email.split("@")[0],
        workspace_dir=workspace,
        tenant_id=args.tenant_id,
        org_local_fallback=org_fallback if org_fallback.exists() else None,
    )

    print(ctx.as_concatenated_markdown())
    if ctx.warnings:
        print("\n# === Warnings ===")
        for w in ctx.warnings:
            print(f"# {w}")
    print(_render_session_protocol(user_email, display_name,
                                                          personal_bc_project_id, basecamp_account_id,
                                                          personal_bc_todolist_id))
    return 0


def _render_session_protocol(user_email: str, display_name: str,
                                                personal_bc_project_id: str,
                                                basecamp_account_id: str,
                                                personal_bc_todolist_id: str = "") -> str:
    """Render the mandatory-ticket Session Protocol Claude sees on every session start.

    Per docs/specs/operator-02-mandatory-ticket-doctrine.md. The protocol tells
    Claude (the LLM running inside the user's workspace) what to do on the user's
    first substantive prompt:

        1. Classify the prompt (substantive vs read-only vs flag-overridden).
        2. If substantive AND no existing BC ticket is referenced: create one in
           the user's personal BC project (id below) BEFORE doing the work.
        3. If substantive AND prompt references a BC ticket (URL or BC#<id>):
           anchor work to that ticket.
        4. Post structured progress cards (Op 3) as comments during the work.
        5. Auto-close at end if confidence >= 0.85 (Op 4).
    """
    bc_known = personal_bc_project_id and basecamp_account_id
    todolist_line = (
        f"- Default todolist id (use as `todolist_id` in create_ticket_for_session): `{personal_bc_todolist_id}`\n"
        if personal_bc_todolist_id else
        "- (Default todolist not provisioned; discover via `GET /buckets/<project>/projects/<project>.json` -> dock['todoset'] -> todolists_url)\n"
    )
    bc_block = (
        f"- Personal BC project id (use as `bucket_id`): `{personal_bc_project_id}`\n"
        f"- Basecamp account id: `{basecamp_account_id}`\n"
        f"- Project URL: https://3.basecamp.com/{basecamp_account_id}/projects/{personal_bc_project_id}\n"
        + todolist_line
        if bc_known else
        "- (Personal BC project id not yet provisioned; Op 2 provision may have failed; admin should check audit log.)\n"
    )
    return f"""

# === Session Protocol (mandatory) - Op 2 ticket doctrine ===
# Source: docs/specs/operator-02-mandatory-ticket-doctrine.md

You are running in {display_name or user_email}'s personal Colaberry workspace.

**Every Claude Code session anchors to exactly one Basecamp ticket** in their personal project.

{bc_block}
**On the user's first prompt, before doing any work:**

1. Classify the prompt. Substantive = anything involving build/implement/create/write/add/update/edit/modify/refactor/fix/deploy/send/delete/install/configure/provision/migrate/seed/rename/commit. Read-only = questions starting with what/how/why/show/explain/list/find/check.

2. If the prompt is **read-only**, just answer. No ticket needed.

3. If the prompt is **substantive**:
   a. Check if the prompt references a BC ticket (URL like `https://3.basecamp.com/.../todos/<id>` or shorthand `BC#<id>`). If yes, anchor work to that ticket (call `fetch_existing_ticket` to load it).
   b. If no ticket referenced, derive a proposed title from the first sentence (~90 chars), tell the user "Before I start, I'll create a Basecamp ticket. Proposed title: <X>. Reply `confirm` or edit the title." Then create the ticket via `create_ticket_for_session()` in the personal BC project above.

4. The helper module lives at: `execution.products.library.ticket_creation_flow` (in the central AI_ProjectArchitect repo if cloned side-by-side; otherwise call the BC API directly via curl/python). Key functions:
   - `classify_prompt(prompt: str) -> PromptClassification`
   - `derive_proposed_title(prompt: str, max_chars: int = 90) -> str`
   - `create_ticket_for_session(title, description, account_id, bucket_id, todolist_id, bc_token)` -- note `bucket_id` is the project id, `todolist_id` is the project's default todo list (discoverable via `GET /buckets/<id>/projects/<id>.json` then look in `dock` array for `name="todoset"`, then GET that todoset for `todolists[0].id`).

5. As the work progresses, post structured HTML progress cards as comments on the ticket (Op 3 doctrine: idempotent `<!-- step:KIND:HASH -->` markers).

6. At end of work: auto-close if confidence >= 0.85 (Op 4); otherwise ask the user to confirm before closing.

**Override flags** the user can type at prompt start:
- `--no-ticket <prompt>` -- skip ticket creation for this session (logs a bypass record)
- `--ticket <prompt>` -- force ticket creation even if prompt looks read-only

# === End of Session Protocol ===
"""


if __name__ == "__main__":
    sys.exit(main())
