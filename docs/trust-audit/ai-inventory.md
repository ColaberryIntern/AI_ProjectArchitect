# Phase 2 — AI System Inventory

**Audit date:** 2026-06-20 · Evidence cited as `path:line`.

## Provider summary

- **LLM provider: OpenAI only** — `gpt-4o-mini` (default, `config/settings.py:43`) and `gpt-4o` (ops workflows, `OPS_LLM_MODEL`/`OPS_PLAN_MODEL`). Centralized wrapper: `execution/llm_client.py` (`is_available()` = `bool(OPENAI_API_KEY)`).
- **No Anthropic/Gemini/Ollama in code.** Note: `CLAUDE.md` (env guidance) recommends defaulting to latest Claude models for AI apps; production code uses OpenAI. → **Finding AI-1 (model-choice divergence)**, non-blocking.
- **No vector embeddings / vector DB.** Retrieval is keyword/TF + graph + reputation (`execution/ops_platform/search_index.py`, `recommendation_engine.py`). RAG-like grounding via context assembly, not embeddings.
- **Prompts** are hardcoded Python constants (no external prompt registry) → **Finding AI-2 (no prompt versioning)**.
- **Resilience:** every LLM call has a deterministic/heuristic fallback (graceful degradation when key absent or call fails).

## AI capability register

For each: purpose · inputs → outputs · owner · approval · observability · **risk**.

### Autonomous / runtime (highest scrutiny)

| # | Capability | File | Model | Acts autonomously? | Observability | Risk |
|---|-----------|------|-------|--------------------|---------------|------|
| A1 | **@CB mention auto-responder** | `execution/products/ops/cb_mention_worker.py` | gpt-4o (via plan_inference) | **Yes** — posts BC comment | heartbeat + seen.json + cursor; **not** in central audit_log | **HIGH** |
| A2 | **Auto-pickup worker** | `execution/products/ops/autopickup_worker.py` | gpt-4o | Draft-only, off by default, allowlist | per-todo JSONL audit + heartbeat | **MEDIUM** |
| A3 | **Advisory pipeline** | `execution/advisory/*` | gpt-4o-mini | Recommend-only; user-driven side effects | event log + session snapshots | **HIGH** (public unauth endpoint) |
| A4 | **Productivity report** | `execution/products/ops/productivity/runner.py` | none (deterministic) | Emails on schedule, off-by-default | HTML+JSON artifacts + logs | **MEDIUM** |

A1: inputs = BC comments mentioning @CB → output = rubric plan comment + confidence. Owner ali@colaberry.com. Approval: none per-post (circuit breaker `MAX_RESPONSES_PER_TICK=12`, loop/bot guards). Why HIGH: autonomous external write, loop risk (mitigated). Attestation: `cb_mention_worker.py.tbi.json` (compliant).
A3: inputs = anonymous web Q&A → outputs = blueprint, lead capture, **BC project create**, **enterprise webhook**, calendar. Why HIGH: unauthenticated endpoint with downstream side effects + no kill-switch (`recommendation_engine.py.tbi.json` notes → P1.5 hardening).

### Interactive build pipeline (human-gated)

| # | Capability | File | Model | Output | Risk |
|---|-----------|------|-------|--------|------|
| B1 | Chapter writer | `execution/chapter_writer.py:139` | gpt-4o-mini @ temp 0.2 | chapter JSON; 3 retries→template | MEDIUM |
| B2 | Feature advisor | `execution/feature_advisor.py:396,454` | gpt-4o-mini | relevance + suggestions; neutral fallback | MEDIUM |
| B3 | Feature catalog | `execution/feature_catalog.py:180` | gpt-4o-mini | discovery list | LOW |
| B4 | Intelligence goals | `execution/intelligence_goals.py:300` | gpt-4o-mini | goals | LOW |
| B5 | Ideation advisor | `execution/ideation_advisor.py:419` | gpt-4o-mini | conversation | MEDIUM |
| B6 | Outline / Profile generators | `execution/outline_generator.py`, `profile_generator.py` | gpt-4o-mini | outline/profile; fallback default | MEDIUM |
| B7 | Semantic judge (quality gates 7-8) | `execution/semantic_judge.py:28` | gpt-4o-mini @ temp 0.0 | AC testability + intern test; cached; advisory-skip if no LLM | LOW |

All B-tier outputs pass the deterministic quality gates (`execution/quality_gate_runner.py`, 5 lexical + 3 spec) before shipping → human approves each gate.

### Advisory engines (within A3)

answer_validator (`advisory/answer_validator.py:49`, permissive fallback), business_interpreter (`:85`), taxonomy_registry (`:120-180`, cached/idempotent — LOW on cache hit), org_builder (`:100`, template fallback), recommendation_engine (deterministic ranking).

### Ops-platform AI

| # | Capability | File | Model | Output | Risk |
|---|-----------|------|-------|--------|------|
| C1 | llm_suggest (My Day) | `execution/products/ops/llm_suggest.py:193` | gpt-4o | action plan, disk-cached; rendered to user (not auto-run) | MEDIUM |
| C2 | plan_inference (Magic Input) | `execution/products/ops/plan_inference.py` | gpt-4o | exec plan + confidence% | MEDIUM |
| C3 | verification_agent | `execution/ops_platform/verification_agent.py:61` | gpt-4o-mini @ 0.0 | structural+semantic verdict; conservative fallback | LOW |
| C4 | training_agent | `execution/ops_platform/training_agent.py:49` | gpt-4o-mini | walkthrough; template fallback | LOW |
| C5 | semantic_analyzer | `execution/ops_platform/semantic_analyzer.py:60` | gpt-4o-mini | 17-field enrichment; heuristic fallback; cached | LOW |
| C6 | ops recommendation_engine | `execution/ops_platform/recommendation_engine.py:85` | none (deterministic) | ranked capabilities | LOW |
| C7 | TBI compliance scorer | `execution/ops_platform/tbi_compliance.py` | none (deterministic) | compliance verdict | LOW |

## Risk distribution

| Risk | Count | Capabilities |
|------|-------|--------------|
| **CRITICAL** | 0 | — (no fully-autonomous irreversible-action AI; all writes are low-risk comments/emails or human-gated) |
| **HIGH** | 2 | A1 @CB responder, A3 advisory |
| **MEDIUM** | 6 | A2, A4, B1, B2, B5, B6, C1, C2 |
| **LOW** | rest | B3,B4,B7,C3-C7, advisory taxonomy (cache hit) |

## Cross-cutting risk controls (present)

- Centralized LLM client + per-call deterministic fallback (no hard failure on LLM outage).
- Autonomy policies + 6-gate runtime enforcement (`agent_runtime.py:80-190`): paused → permitted-action → confidence → rollback-plan → policy → maintenance/freeze.
- TBI attestation required for every AI artifact (CI-gated).
- Quality gates for all generated build content.

## Owner / approval / observability gaps (→ gap-analysis.md)

- **Owner metadata** is implicit (ali@colaberry.com via attestations); no per-capability owner field surfaced in a registry UI. **Finding AI-3**.
- **Cost per AI call** captured as tokens in `RunRecord.llm_usage` but no persistent cost ledger (see observability-audit Cost dim). **Finding AI-4**.
- **A1/A3 external writes** (BC comment, webhook, email) not recorded in the central `audit_log` (worker-local logs only). **Finding AI-5** (auditability).
