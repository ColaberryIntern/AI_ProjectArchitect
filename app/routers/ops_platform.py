"""HTTP surface for the AI Operations Platform.

All routes mounted under /ops. The router is purposely thin: it does
validation + parameter parsing + template rendering, and delegates the
real work to execution.ops_platform.*.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse

from execution.ops_platform import (
    access_reviews,
    adoption,
    agent_registry,
    agent_runtime,
    alerts,
    analytics,
    approvals,
    audit_log,
    auth,
    backup_restore,
    builder,
    capability_registry,
    capability_versions,
    change_requests,
    chaos_engine,
    collab_sessions,
    compliance_reports,
    controls,
    coordination_diagnostics,
    copilot,
    distributed_event_bus,
    distributed_lock_v2,
    distributed_presence,
    event_fabric,
    orchestration_runtime,
    projection_engine,
    backup_integrity,
    load_test,
    orchestration_recovery,
    poison_handler,
    recovery_coordinator,
    redis_sentinel,
    transactional_outbox,
    discovery_queue,
    distributed_lock,
    distributed_rate_limit,
    enforcement,
    evaluation,
    execution_assistant,
    executive_reporting,
    experiments,
    feedback_store,
    forecasting,
    governance_scorecards,
    incidents,
    knowledge_graph,
    marketplace,
    migrations,
    notifications,
    operational_graph,
    optimistic_concurrency,
    orchestration_engine,
    organizational_memory,
    pipeline_engine,
    policy_engine,
    presence,
    prompt_diff,
    prometheus_exporter,
    rbac,
    realtime_bus,
    recommendation_engine,
    redis_backends,
    reliability_monitor,
    reputation_scorer,
    requirements_intelligence,
    retention_policy,
    runtime_queue,
    runtime_router,
    scheduler,
    scoped_memory,
    search_index,
    security_telemetry,
    self_healing,
    semantic_analyzer,
    service_identities,
    signed_audit,
    telemetry,
    tracing,
    training_agent,
    training_pipeline,
    trust_engine,
    verification_agent,
    worker_coordination,
    workflow_discovery,
    workflow_optimizer,
    workflow_runner,
    workspaces,
    ws_gateway,
)
from fastapi import WebSocket, WebSocketDisconnect
from execution.ops_platform.errors import OpsError, not_found, invalid_input, conflict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ops", tags=["ops_platform"])


# ── HTML pages ──────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, q: str = ""):
    """Search-first homepage. Renders trending + recommended capabilities."""
    reg = capability_registry.default_registry()
    snap = reg.snapshot()

    results = []
    if q:
        results = search_index.search(q, top_k=20, registry=reg)

    trending = sorted(
        snap.capabilities,
        key=lambda c: (-int(c.get("usage_count", 0)), c.get("name", "")),
    )[:8]

    departments = snap.departments()

    # Phase 2 widgets — keep cheap, never block the page render
    try:
        recommendations = recommendation_engine.recommend(query=q, top_k=6)
        recommendations = [r.to_dict() for r in recommendations]
    except Exception:
        logger.warning("recommendation widget failed", exc_info=True)
        recommendations = []

    try:
        summary = analytics.executive_summary()
    except Exception:
        logger.warning("analytics widget failed", exc_info=True)
        summary = None

    return request.app.state.templates.TemplateResponse(
        request,
        "ops/home.html",
        {
            "query": q,
            "results": results,
            "trending": trending,
            "departments": departments,
            "capability_count": len(snap.capabilities),
            "load_errors": snap.errors,
            "recommendations": recommendations,
            "summary": summary,
        },
    )


@router.get("/capabilities/{capability_id}", response_class=HTMLResponse)
async def capability_detail(request: Request, capability_id: str):
    reg = capability_registry.default_registry()
    capability = reg.get(capability_id)
    if capability is None:
        raise HTTPException(status_code=404, detail=f"capability '{capability_id}' not found")

    aggregate = feedback_store.get_aggregate(capability_id)
    recent_feedback = feedback_store.list_feedback(capability_id)[:5]
    related = search_index.recommend_related(capability_id, top_k=5, registry=reg)
    runs = workflow_runner.list_runs(capability_id=capability_id, limit=5)
    training_md = training_agent.get_training_markdown(capability_id)

    return request.app.state.templates.TemplateResponse(
        request,
        "ops/capability_detail.html",
        {
            "capability": capability,
            "aggregate": aggregate,
            "recent_feedback": recent_feedback,
            "related": related,
            "recent_runs": runs,
            "training_markdown": training_md,
        },
    )


@router.get("/run/{capability_id}", response_class=HTMLResponse)
async def run_form(request: Request, capability_id: str):
    reg = capability_registry.default_registry()
    capability = reg.get(capability_id)
    if capability is None:
        raise HTTPException(status_code=404, detail=f"capability '{capability_id}' not found")

    return request.app.state.templates.TemplateResponse(
        request,
        "ops/workflow_run.html",
        {"capability": capability, "run": None},
    )


# ── JSON / form-handling endpoints ──────────────────────────────────────


@router.post("/run/{capability_id}")
async def run_workflow(request: Request, capability_id: str):
    """Launch a workflow. Body is form-data with one field per declared input."""
    form_data = await request.form()
    inputs = {k: str(v) for k, v in form_data.items()}
    run = workflow_runner.run_workflow(capability_id, inputs)
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse(content=run.to_dict())
    capability = capability_registry.default_registry().get(capability_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "ops/workflow_run.html",
        {"capability": capability, "run": run.to_dict()},
    )


@router.get("/run/{run_id}/result")
async def run_result(run_id: str):
    """JSON details of a previously executed run."""
    run = workflow_runner.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    return JSONResponse(content=run.to_dict())


@router.get("/verify/{run_id}")
async def verify_run(run_id: str, use_llm: bool = True):
    result = verification_agent.verify_run(run_id, use_llm=use_llm)
    return JSONResponse(content={
        "payload": result.payload,
        "structural_findings": result.structural_findings,
        "llm_used": result.llm_used,
        "errors": result.errors,
    })


@router.post("/training/{capability_id}/generate")
async def regenerate_training(capability_id: str):
    try:
        result = training_agent.generate_training(capability_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(content={
        "capability_id": result.capability_id,
        "output_path": result.output_path,
        "markdown_chars": len(result.markdown),
        "llm_used": result.llm_used,
    })


@router.post("/feedback")
async def submit_feedback(request: Request):
    """Submit a feedback record. JSON body matching feedback.schema.json."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="body must be JSON")
    try:
        record = feedback_store.submit_feedback(body)
    except feedback_store.FeedbackInvalid as e:
        raise HTTPException(status_code=422, detail=e.errors)
    return JSONResponse(content=record)


@router.get("/feedback/{capability_id}")
async def get_feedback(capability_id: str):
    return JSONResponse(content={
        "aggregate": feedback_store.get_aggregate(capability_id),
        "records": feedback_store.list_feedback(capability_id),
    })


@router.get("/search")
async def search_endpoint(q: str, type: str | None = None, category: str | None = None,
                          top_k: int = 20):
    results = search_index.search(q, top_k=top_k, type_filter=type, category_filter=category)
    return JSONResponse(content={
        "query": q,
        "count": len(results),
        "results": [
            {
                "capability_id": r.capability_id,
                "score": r.score,
                "matched_tokens": r.matched_tokens,
                "name": r.capability.get("name"),
                "type": r.capability.get("type"),
                "category": r.capability.get("category"),
                "description": r.capability.get("description"),
            }
            for r in results
        ],
    })


@router.get("/intelligence/aggregate")
async def intelligence_aggregate():
    """Return the cross-run reusable-pattern aggregate. Used by the project builder."""
    return JSONResponse(content=requirements_intelligence.load_aggregate())


@router.get("/intelligence/suggest/{slug}")
async def intelligence_suggest(slug: str, top_n: int = 10):
    """Produce candidate Requirement objects for a project from the aggregate."""
    suggestions = requirements_intelligence.feed_into_project(slug, top_n=top_n)
    return JSONResponse(content={"slug": slug, "suggestions": suggestions})


@router.post("/registry/refresh")
async def refresh_registry():
    """Force a registry reload + search index rebuild. Idempotent."""
    reg = capability_registry.default_registry()
    snap = reg.refresh()
    search_index.reset_index()
    indexed = search_index.rebuild(reg)
    recommendation_engine.reset_graph_cache()
    return JSONResponse(content={
        "capability_count": len(snap.capabilities),
        "indexed": indexed,
        "errors": snap.errors,
        "skipped": snap.skipped,
    })


# ── Phase 2: pipelines, recommendations, graph, analytics, discovery ───


@router.get("/pipelines", response_class=HTMLResponse)
async def pipelines_page(request: Request):
    pipelines = pipeline_engine.list_pipelines()
    health = analytics.pipeline_health()
    return request.app.state.templates.TemplateResponse(
        request,
        "ops/pipelines.html",
        {"pipelines": pipelines, "health": {h["pipeline_id"]: h for h in health}},
    )


@router.get("/pipelines/{pipeline_id}", response_class=HTMLResponse)
async def pipeline_detail(request: Request, pipeline_id: str):
    manifest = pipeline_engine.load_pipeline(pipeline_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"pipeline '{pipeline_id}' not found")
    recent_runs = pipeline_engine.list_pipeline_runs(pipeline_id=pipeline_id, limit=10)
    return request.app.state.templates.TemplateResponse(
        request,
        "ops/pipeline_detail.html",
        {
            "pipeline": manifest,
            "recent_runs": [r.to_dict() for r in recent_runs],
        },
    )


@router.post("/pipelines/{pipeline_id}/run")
async def run_pipeline_endpoint(request: Request, pipeline_id: str):
    form_data = await request.form()
    inputs = {k: str(v) for k, v in form_data.items()}
    initiator = {"name": str(inputs.pop("__initiator", "anonymous"))}
    record = pipeline_engine.run_pipeline(pipeline_id, inputs, initiator=initiator)
    return JSONResponse(content=record.to_dict())


@router.get("/pipelines/runs/{pipeline_run_id}")
async def pipeline_run_detail(pipeline_run_id: str):
    record = pipeline_engine.get_pipeline_run(pipeline_run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="pipeline run not found")
    return JSONResponse(content=record.to_dict())


@router.get("/recommend", response_class=HTMLResponse)
async def recommend_page(
    request: Request,
    q: str = "",
    role: str | None = None,
    department: str | None = None,
):
    recs = recommendation_engine.recommend(
        query=q, role=role, department=department, top_k=10
    )
    pipeline_recs = recommendation_engine.recommend_pipelines_for_query(q, top_k=5) if q else []
    reg = capability_registry.default_registry()
    return request.app.state.templates.TemplateResponse(
        request,
        "ops/recommend.html",
        {
            "query": q,
            "role": role,
            "department": department,
            "departments": reg.snapshot().departments(),
            "recommendations": [r.to_dict() for r in recs],
            "pipeline_recommendations": pipeline_recs,
        },
    )


@router.get("/recommend/api")
async def recommend_api(
    q: str = "",
    role: str | None = None,
    department: str | None = None,
    top_k: int = 8,
):
    recs = recommendation_engine.recommend(
        query=q, role=role, department=department, top_k=top_k
    )
    return JSONResponse(content={
        "query": q, "count": len(recs),
        "recommendations": [r.to_dict() for r in recs],
    })


@router.get("/recommend/next/{run_id}")
async def recommend_next(run_id: str, top_k: int = 5):
    recs = recommendation_engine.recommend_next_after_run(run_id, top_k=top_k)
    return JSONResponse(content={
        "anchor_run": run_id,
        "recommendations": [r.to_dict() for r in recs],
    })


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    summary = analytics.executive_summary()
    return request.app.state.templates.TemplateResponse(
        request,
        "ops/analytics.html",
        {
            "summary": summary,
            "department_usage": analytics.department_usage(),
            "roi": analytics.automation_roi(),
            "bottlenecks": analytics.bottlenecks(),
            "training_gaps": analytics.training_gaps(),
            "feedback_pulse": analytics.feedback_pulse(),
            "pipeline_health": analytics.pipeline_health(),
            "top_by_growth": analytics.top_capabilities(by="growth", top_n=10),
        },
    )


@router.get("/analytics/summary")
async def analytics_summary_api():
    return JSONResponse(content=analytics.executive_summary())


@router.get("/graph")
async def graph_endpoint(persist: bool = False):
    g = operational_graph.build_graph(persist=persist)
    return JSONResponse(content=g.to_dict())


@router.get("/semantic/{capability_id}")
async def semantic_endpoint(capability_id: str, refresh: bool = False):
    reg = capability_registry.default_registry()
    cap = reg.get(capability_id)
    if cap is None:
        raise HTTPException(status_code=404, detail="capability not found")
    if refresh:
        result = semantic_analyzer.enrich_capability(cap, force_refresh=True)
    else:
        existing = semantic_analyzer.load_enrichment(capability_id)
        if existing is not None:
            return JSONResponse(content={"capability_id": capability_id,
                                         "from_cache": True, "payload": existing})
        result = semantic_analyzer.enrich_capability(cap)
    return JSONResponse(content={
        "capability_id": capability_id,
        "from_cache": result.from_cache,
        "source": result.source,
        "payload": result.payload,
    })


@router.post("/semantic/enrich-all")
async def enrich_all_endpoint(force: bool = False):
    results = semantic_analyzer.enrich_all(force_refresh=force)
    return JSONResponse(content={
        "enriched": len(results),
        "sources": {r.source: sum(1 for x in results.values() if x.source == r.source)
                    for r in results.values()},
    })


@router.get("/semantic/duplicates")
async def semantic_duplicates():
    return JSONResponse(content=semantic_analyzer.detect_duplicates())


@router.get("/reputation/{capability_id}")
async def reputation_endpoint(capability_id: str):
    score = reputation_scorer.score_capability(capability_id)
    return JSONResponse(content=score.to_dict())


@router.get("/reputation")
async def reputation_ranked(top_n: int = 25):
    return JSONResponse(content=[s.to_dict() for s in reputation_scorer.ranked(top_n=top_n)])


@router.get("/discoveries")
async def discoveries_endpoint(
    window: int = 3,
    min_occurrences: int = 3,
    persist: bool = False,
):
    patterns = workflow_discovery.discover_patterns(
        window=window, min_occurrences=min_occurrences
    )
    if persist:
        workflow_discovery.snapshot_discoveries(patterns)
    return JSONResponse(content={
        "count": len(patterns),
        "patterns": [p.to_dict() for p in patterns],
    })


# ── Phase 3: discovery queue, optimizer, assistant, memory, training_pipeline ──


@router.get("/discovery-queue", response_class=HTMLResponse)
async def discovery_queue_page(request: Request, state: str | None = None):
    workflow_discovery.record_to_queue(workflow_discovery.discover_patterns())
    items = discovery_queue.list_items(state=state)
    return request.app.state.templates.TemplateResponse(
        request,
        "ops/discovery_queue.html",
        {"items": [i.to_dict() for i in items], "state": state,
         "stats": discovery_queue.queue_stats()},
    )


@router.post("/discovery-queue/{queue_id}/approve")
async def discovery_queue_approve(queue_id: str, request: Request):
    body = (await request.form()) if "form" in request.headers.get("content-type", "") else {}
    reviewer = body.get("reviewer") if body else None
    notes = body.get("notes") if body else None
    item = discovery_queue.approve(queue_id, reviewer=reviewer, notes=notes)
    if item is None:
        raise HTTPException(status_code=404, detail="queue item not found")
    return JSONResponse(content=item.to_dict())


@router.post("/discovery-queue/{queue_id}/reject")
async def discovery_queue_reject(queue_id: str, request: Request):
    body = (await request.form()) if "form" in request.headers.get("content-type", "") else {}
    reviewer = body.get("reviewer") if body else None
    notes = body.get("notes") if body else None
    item = discovery_queue.reject(queue_id, reviewer=reviewer, notes=notes)
    if item is None:
        raise HTTPException(status_code=404, detail="queue item not found")
    return JSONResponse(content=item.to_dict())


@router.post("/discovery-queue/{queue_id}/publish")
async def discovery_queue_publish(queue_id: str):
    item, err = discovery_queue.publish(queue_id)
    if item is None:
        raise HTTPException(status_code=404, detail="queue item not found")
    if err:
        return JSONResponse(status_code=422, content={"error": err, "item": item.to_dict()})
    return JSONResponse(content=item.to_dict())


@router.get("/assistant/prepare/{capability_id}")
async def assistant_prepare(capability_id: str, role: str | None = None):
    try:
        result = execution_assistant.prepare(capability_id, role=role)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(content=result.to_dict())


@router.get("/assistant/explain/{run_id}")
async def assistant_explain(run_id: str):
    result = execution_assistant.explain_output(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return JSONResponse(content=result.to_dict())


@router.get("/assistant", response_class=HTMLResponse)
async def assistant_page(request: Request, intent: str = "", role: str | None = None,
                          department: str | None = None):
    recs = []
    if intent:
        recs = execution_assistant.intent_to_capabilities(
            intent, role=role, department=department, top_k=6,
        )
    return request.app.state.templates.TemplateResponse(
        request,
        "ops/assistant.html",
        {"intent": intent, "role": role, "department": department,
         "recommendations": recs},
    )


@router.get("/memory")
async def memory_endpoint(rebuild: bool = False):
    if rebuild:
        snap = organizational_memory.build_snapshot(persist=True)
        return JSONResponse(content=snap.to_dict())
    cached = organizational_memory.latest_snapshot()
    if cached is None:
        snap = organizational_memory.build_snapshot(persist=True)
        return JSONResponse(content=snap.to_dict())
    return JSONResponse(content=cached)


@router.get("/optimizer/suggestions")
async def optimizer_endpoint():
    return JSONResponse(content=[s.to_dict() for s in workflow_optimizer.analyze()])


@router.get("/optimizer", response_class=HTMLResponse)
async def optimizer_page(request: Request):
    suggestions = workflow_optimizer.analyze()
    return request.app.state.templates.TemplateResponse(
        request,
        "ops/optimizer.html",
        {"suggestions": [s.to_dict() for s in suggestions]},
    )


@router.post("/training/assets/{capability_id}")
async def training_assets_generate(capability_id: str):
    try:
        bundle = training_pipeline.generate_assets(capability_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(content=bundle.to_dict())


@router.get("/training/assets/{capability_id}")
async def training_assets_get(capability_id: str):
    bundle = training_pipeline.get_bundle(capability_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="no asset bundle yet")
    return JSONResponse(content=bundle)


@router.get("/analytics/abandonment")
async def analytics_abandonment_api():
    return JSONResponse(content=analytics.abandonment_analysis())


@router.get("/analytics/duration")
async def analytics_duration_api():
    return JSONResponse(content=analytics.duration_analysis())


@router.get("/analytics/roi-trend")
async def analytics_roi_trend_api(bucket_days: int = 7, buckets: int = 8):
    return JSONResponse(content=analytics.roi_trend(bucket_days=bucket_days, buckets=buckets))


@router.get("/analytics/adoption")
async def analytics_adoption_api():
    return JSONResponse(content=analytics.department_adoption_curve())


@router.get("/analytics/heatmap")
async def analytics_heatmap_api():
    return JSONResponse(content=analytics.workflow_dependency_heatmap())


@router.get("/analytics/training-effectiveness")
async def analytics_training_effectiveness_api():
    return JSONResponse(content=analytics.training_effectiveness())


@router.get("/semantic/anti-patterns")
async def semantic_anti_patterns_api():
    return JSONResponse(content=semantic_analyzer.detect_anti_patterns())


@router.get("/semantic/overlap")
async def semantic_overlap_api(threshold: float = 0.4):
    return JSONResponse(content=semantic_analyzer.workflow_overlap(threshold=threshold))


@router.get("/semantic/patterns")
async def semantic_patterns_api():
    return JSONResponse(content=semantic_analyzer.operational_patterns())


# ── Phase 4: errors, versioning, prompt diff, audit, workspaces,
#            builder, optimizer apply, reporting, telemetry, adoption ──


def _ops_error(err: OpsError) -> JSONResponse:
    return JSONResponse(status_code=err.status_code, content=err.to_dict())


# Capability versioning -------------------------------------------------

@router.get("/capabilities/{capability_id}/versions")
async def list_capability_versions(capability_id: str):
    versions = capability_versions.list_versions(capability_id)
    return JSONResponse(content=[v.to_dict() for v in versions])


@router.post("/capabilities/{capability_id}/versions")
async def create_capability_version(capability_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    semver = body.get("semver")
    if not semver:
        return _ops_error(invalid_input("semver is required"))
    try:
        version = capability_versions.register_version(
            capability_id,
            semver=semver,
            changelog=body.get("changelog", ""),
            created_by=body.get("created_by", {"name": "anonymous"}),
            parent_version_id=body.get("parent_version_id"),
            status=body.get("status", "draft"),
            rollout_percentage=body.get("rollout_percentage", 0.0),
            migration_notes=body.get("migration_notes", ""),
            compatibility_notes=body.get("compatibility_notes", ""),
            manifest_snapshot=body.get("manifest_snapshot"),
            prompt_snapshot=body.get("prompt_snapshot"),
            tags=body.get("tags") or [],
        )
    except ValueError as e:
        return _ops_error(invalid_input(str(e)))
    return JSONResponse(content=version.to_dict())


@router.post("/capabilities/{capability_id}/promote")
async def promote_capability_version(capability_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    version_id = body.get("version_id")
    target_status = body.get("status")
    if not version_id or not target_status:
        return _ops_error(invalid_input("version_id and status are required"))
    try:
        result = capability_versions.promote(
            version_id, target_status=target_status,
            approver=body.get("approver"),
            rollout_percentage=body.get("rollout_percentage"),
        )
    except ValueError as e:
        return _ops_error(invalid_input(str(e)))
    if result is None:
        return _ops_error(not_found("capability_version", version_id))
    return JSONResponse(content=result.to_dict())


@router.post("/capabilities/{capability_id}/rollback")
async def rollback_capability_version(capability_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    target_version_id = body.get("target_version_id")
    if not target_version_id:
        return _ops_error(invalid_input("target_version_id is required"))
    result = capability_versions.rollback(
        capability_id, target_version_id=target_version_id,
        actor=body.get("actor"),
    )
    if result is None:
        return _ops_error(not_found("capability_version", target_version_id))
    return JSONResponse(content=result.to_dict())


@router.get("/capabilities/{capability_id}/compare")
async def compare_capability_versions(capability_id: str, v1: str, v2: str):
    diff = capability_versions.compare(v1, v2)
    if diff.get("error"):
        return _ops_error(not_found("capability_version", f"{v1}|{v2}"))
    return JSONResponse(content=diff)


@router.get("/capabilities/{capability_id}/versions/page", response_class=HTMLResponse)
async def capability_versions_page(request: Request, capability_id: str):
    reg = capability_registry.default_registry()
    capability = reg.get(capability_id)
    versions = capability_versions.list_versions(capability_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "ops/versions.html",
        {"capability": capability, "capability_id": capability_id,
         "versions": [v.to_dict() for v in versions]},
    )


# Prompt + execution diff ----------------------------------------------

@router.get("/prompt-diff")
async def prompt_diff_api(v1: str, v2: str):
    d = prompt_diff.diff_prompts(v1, v2)
    if d is None:
        return _ops_error(not_found("capability_version", f"{v1}|{v2}"))
    return JSONResponse(content=d.to_dict())


@router.get("/execution-diff")
async def execution_diff_api(v1: str, v2: str):
    d = prompt_diff.diff_executions(v1, v2)
    if d is None:
        return _ops_error(not_found("capability_version", f"{v1}|{v2}"))
    return JSONResponse(content=d.to_dict())


# Audit ------------------------------------------------------------------

@router.get("/audit")
async def audit_list(
    days: int = 7,
    entity_id: str | None = None,
    entity_type: str | None = None,
    action: str | None = None,
    actor_name: str | None = None,
    correlation_id: str | None = None,
    limit: int = 200,
):
    return JSONResponse(content=audit_log.list_entries(
        days=days, entity_id=entity_id, entity_type=entity_type,
        action=action, actor_name=actor_name, correlation_id=correlation_id,
        limit=limit,
    ))


@router.get("/audit/entity/{entity_id}")
async def audit_entity(entity_id: str, days: int = 90):
    return JSONResponse(content=audit_log.entity_history(entity_id, days=days))


@router.get("/audit/stats")
async def audit_stats(days: int = 7):
    return JSONResponse(content=audit_log.stats(days=days))


@router.get("/audit/page", response_class=HTMLResponse)
async def audit_page(request: Request, days: int = 7):
    entries = audit_log.list_entries(days=days, limit=200)
    return request.app.state.templates.TemplateResponse(
        request,
        "ops/audit.html",
        {"entries": entries, "days": days,
         "stats": audit_log.stats(days=days)},
    )


# Workspaces -------------------------------------------------------------

@router.get("/workspaces")
async def list_workspaces_api():
    return JSONResponse(content=[w.to_dict() for w in workspaces.list_workspaces()])


@router.post("/workspaces")
async def create_workspace_api(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        ws = workspaces.create_workspace(
            workspace_id=body["workspace_id"],
            name=body["name"],
            owner=body.get("owner") or {"name": "anonymous"},
            department=body.get("department", ""),
            visibility=body.get("visibility", "internal"),
            tags=body.get("tags") or [],
            capability_ids=body.get("capability_ids") or [],
            pipeline_ids=body.get("pipeline_ids") or [],
            description=body.get("description", ""),
        )
    except (KeyError, ValueError) as e:
        return _ops_error(invalid_input(str(e)))
    return JSONResponse(content=ws.to_dict())


@router.get("/workspaces/{workspace_id}")
async def get_workspace_api(workspace_id: str):
    ws = workspaces.get_workspace(workspace_id)
    if ws is None:
        return _ops_error(not_found("workspace", workspace_id))
    return JSONResponse(content=ws.to_dict())


@router.post("/workspaces/{workspace_id}/attach")
async def workspace_attach_api(workspace_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    if body.get("capability_id"):
        ws = workspaces.attach_capability(workspace_id, body["capability_id"],
                                          actor=body.get("actor"))
    elif body.get("pipeline_id"):
        ws = workspaces.attach_pipeline(workspace_id, body["pipeline_id"],
                                        actor=body.get("actor"))
    else:
        return _ops_error(invalid_input("provide capability_id or pipeline_id"))
    if ws is None:
        return _ops_error(not_found("workspace", workspace_id))
    return JSONResponse(content=ws.to_dict())


@router.get("/workspaces/page", response_class=HTMLResponse)
async def workspaces_page(request: Request):
    ws_list = workspaces.list_workspaces()
    return request.app.state.templates.TemplateResponse(
        request,
        "ops/workspaces.html",
        {"workspaces": [w.to_dict() for w in ws_list]},
    )


# Builder ----------------------------------------------------------------

@router.get("/builder", response_class=HTMLResponse)
async def builder_page(request: Request, intent: str = "", role: str | None = None,
                       department: str | None = None, workspace: str | None = None):
    draft = None
    if intent:
        try:
            d = builder.generate(intent, role=role, department=department,
                                  workspace_id=workspace)
            draft = d.to_dict()
        except ValueError:
            draft = None
    drafts = builder.list_drafts(limit=10)
    return request.app.state.templates.TemplateResponse(
        request,
        "ops/builder.html",
        {"intent": intent, "draft": draft, "drafts": drafts,
         "role": role, "department": department, "workspace": workspace},
    )


@router.post("/builder/generate")
async def builder_generate_api(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    intent = body.get("intent", "")
    try:
        draft = builder.generate(intent,
                                  role=body.get("role"),
                                  department=body.get("department"),
                                  workspace_id=body.get("workspace_id"),
                                  polish=bool(body.get("polish", False)))
    except ValueError as e:
        return _ops_error(invalid_input(str(e)))
    return JSONResponse(content=draft.to_dict())


@router.post("/builder/publish")
async def builder_publish_api(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    draft_id = body.get("draft_id")
    if not draft_id:
        return _ops_error(invalid_input("draft_id is required"))
    result = builder.publish_draft(draft_id, actor=body.get("actor"))
    if result.get("error") == "draft not found":
        return _ops_error(not_found("draft", draft_id))
    if "error" in result:
        return _ops_error(invalid_input(result["error"]))
    return JSONResponse(content=result)


# Optimizer simulate + apply --------------------------------------------

@router.get("/optimizer/simulate/{suggestion_id:path}")
async def optimizer_simulate_api(suggestion_id: str):
    sim = workflow_optimizer.simulate(suggestion_id)
    if sim is None:
        return _ops_error(not_found("optimizer_suggestion", suggestion_id))
    return JSONResponse(content=sim.to_dict())


@router.post("/optimizer/apply/{suggestion_id:path}")
async def optimizer_apply_api(suggestion_id: str, request: Request):
    actor = None
    try:
        body = await request.json()
        actor = body.get("actor")
    except Exception:
        actor = None
    result = workflow_optimizer.apply(suggestion_id, actor=actor)
    if result.get("status") == "not_found":
        return _ops_error(not_found("optimizer_suggestion", suggestion_id))
    if result.get("status") in ("failed", "rejected"):
        return _ops_error(invalid_input(result.get("error", "apply failed")))
    return JSONResponse(content=result)


# Executive reporting ---------------------------------------------------

@router.get("/reporting/executive")
async def reporting_executive_api():
    return JSONResponse(content=executive_reporting.executive_scorecard().to_dict())


@router.get("/reporting/monthly")
async def reporting_monthly_api(year: int | None = None, month: int | None = None,
                                 persist: bool = True):
    from datetime import datetime as _dt
    now = _dt.utcnow()
    year = year or now.year
    month = month or now.month
    return JSONResponse(content=executive_reporting.monthly_report(year, month, persist=persist))


@router.get("/reporting/monthly/list")
async def reporting_monthly_list_api():
    return JSONResponse(content=executive_reporting.list_monthly_reports())


@router.get("/reporting/executive/page", response_class=HTMLResponse)
async def reporting_executive_page(request: Request):
    scorecard = executive_reporting.executive_scorecard()
    return request.app.state.templates.TemplateResponse(
        request,
        "ops/executive.html",
        {"scorecard": scorecard.to_dict()},
    )


# System health + telemetry ---------------------------------------------

@router.get("/system/health")
async def system_health_api():
    return JSONResponse(content=telemetry.health_summary().to_dict())


@router.get("/system/metrics")
async def system_metrics_api():
    return JSONResponse(content={
        "latency": telemetry.latency_stats(),
        "token_usage": telemetry.token_usage(),
        "dependency_health": telemetry.dependency_health(),
        "cache_freshness_seconds": telemetry.cache_freshness_seconds(),
        "recommendation_freshness": telemetry.recommendation_freshness(),
        "executions_heatmap": telemetry.executions_heatmap(),
    })


@router.post("/system/snapshot")
async def system_snapshot_api():
    path = telemetry.snapshot()
    return JSONResponse(content={"snapshot_path": str(path)})


# Adoption helpers ------------------------------------------------------

@router.get("/adoption/badges/{capability_id}")
async def adoption_badges_api(capability_id: str):
    return JSONResponse(content=adoption.badges_for(capability_id))


@router.get("/adoption/trust/{capability_id}")
async def adoption_trust_api(capability_id: str):
    return JSONResponse(content={
        "badges": adoption.badges_for(capability_id),
        "confidence": adoption.confidence_indicator(capability_id),
        "estimated_completion_time": adoption.estimated_completion_time(capability_id),
        "common_mistakes": adoption.common_mistakes(capability_id),
        "next_action": adoption.next_action_for(capability_id),
    })


# ── Phase 5: auth, RBAC, runtime routing, scoped memory,
#            marketplace, trust engine, controls ──


def _identity(request: Request):
    return auth.get_identity_context(request)


# Auth ------------------------------------------------------------------

@router.get("/auth/me")
async def auth_me(request: Request):
    identity = _identity(request)
    return JSONResponse(content=identity.to_dict())


@router.post("/auth/login")
async def auth_login(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    user_id = body.get("user_id")
    if not user_id:
        auth.login_failed(user_id=body.get("user_id", ""), reason="missing user_id")
        return _ops_error(invalid_input("user_id is required"))
    identity = auth.login(
        user_id=user_id, display_name=body.get("display_name", ""),
        email=body.get("email", ""), department=body.get("department", ""),
        roles=body.get("roles") or ["viewer"],
        workspace_ids=body.get("workspace_ids") or [],
        auth_provider=body.get("auth_provider", "HEADER_AUTH"),
        ip=request.client.host if request.client else None,
    )
    return JSONResponse(content=identity.to_dict())


@router.post("/auth/logout")
async def auth_logout(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    session_id = body.get("session_id") or request.headers.get("X-Session-Id", "")
    removed = auth.logout(session_id)
    return JSONResponse(content={"removed": removed, "session_id": session_id})


# RBAC ------------------------------------------------------------------

@router.get("/rbac/permissions")
async def rbac_permissions_api():
    return JSONResponse(content={
        "permissions": sorted(rbac.PERMISSIONS),
        "roles": {role: sorted(perms) for role, perms in rbac._ROLE_PERMISSIONS.items()},
        "enforced": rbac.is_enforced(),
    })


@router.get("/rbac/effective-access")
async def rbac_effective_access_api(request: Request):
    identity = _identity(request)
    return JSONResponse(content={
        "identity": identity.to_dict(),
        "effective_permissions": sorted(rbac.effective_permissions(identity)),
        "enforced": rbac.is_enforced(),
    })


# Runtime router --------------------------------------------------------

@router.get("/capabilities/{capability_id}/routing")
async def routing_api(capability_id: str, session_id: str = ""):
    decision = runtime_router.route(capability_id, session_id=session_id,
                                     record_audit=False)
    return JSONResponse(content=decision.to_dict())


@router.post("/capabilities/{capability_id}/routing/simulate")
async def routing_simulate_api(capability_id: str, samples: int = 1000):
    return JSONResponse(content=runtime_router.simulate(capability_id, samples=samples))


# Scoped memory ---------------------------------------------------------

@router.get("/memory/{workspace_id}")
async def scoped_memory_api(request: Request, workspace_id: str, rebuild: bool = False):
    identity = _identity(request)
    if rebuild or scoped_memory.latest_for_workspace(workspace_id) is None:
        snap = scoped_memory.build_for_workspace(workspace_id, identity=identity,
                                                    persist=True)
    else:
        snap = scoped_memory.latest_for_workspace(workspace_id)
    return JSONResponse(content=snap)


@router.get("/workspaces/{workspace_id}/insights")
async def workspace_insights_api(workspace_id: str):
    insights = scoped_memory.workspace_insights(workspace_id)
    if insights.get("error"):
        return _ops_error(not_found("workspace", workspace_id))
    return JSONResponse(content=insights)


# Marketplace -----------------------------------------------------------

@router.get("/marketplace")
async def marketplace_list_api(category: str | None = None,
                                template_kind: str | None = None):
    templates = marketplace.list_templates(category=category, template_kind=template_kind)
    return JSONResponse(content=[t.to_dict() for t in templates])


@router.post("/marketplace/publish")
async def marketplace_publish_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    enforcement.enforce(identity, "marketplace.publish")
    kind = body.get("template_kind") or body.get("kind") or "capability"
    try:
        if kind == "pipeline":
            tpl = marketplace.publish_pipeline_template(
                pipeline_id=body["pipeline_id"], title=body["title"],
                category=body.get("category", "Uncategorized"),
                tags=body.get("tags") or [], trust_badges=body.get("trust_badges") or [],
                estimated_setup_minutes=body.get("estimated_setup_minutes", 10),
                compatibility_notes=body.get("compatibility_notes", ""),
                published_by=identity.as_actor(),
            )
        else:
            tpl = marketplace.publish_capability_template(
                capability_id=body["capability_id"], title=body["title"],
                category=body.get("category", "Uncategorized"),
                tags=body.get("tags") or [], trust_badges=body.get("trust_badges") or [],
                estimated_setup_minutes=body.get("estimated_setup_minutes", 5),
                compatibility_notes=body.get("compatibility_notes", ""),
                published_by=identity.as_actor(),
                version_id=body.get("version_id"),
            )
    except (KeyError, ValueError) as e:
        return _ops_error(invalid_input(str(e)))
    return JSONResponse(content=tpl.to_dict())


@router.post("/marketplace/fork")
async def marketplace_fork_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    template_id = body.get("template_id")
    if not template_id:
        return _ops_error(invalid_input("template_id is required"))
    enforcement.enforce(identity, "marketplace.fork",
                          workspace_id=body.get("workspace_id"))
    result = marketplace.fork(template_id, workspace_id=body.get("workspace_id"),
                                actor=identity.as_actor())
    if result.get("error"):
        return _ops_error(not_found("template", template_id))
    return JSONResponse(content=result)


@router.get("/marketplace/{template_id}")
async def marketplace_get_api(template_id: str):
    tpl = marketplace.get_template(template_id)
    if tpl is None:
        return _ops_error(not_found("template", template_id))
    return JSONResponse(content=tpl.to_dict())


# Trust engine ----------------------------------------------------------

@router.get("/trust/{capability_id}")
async def trust_capability_api(capability_id: str):
    return JSONResponse(content=trust_engine.score(capability_id).to_dict())


@router.get("/trust/report")
async def trust_report_api(top_n: int | None = None):
    return JSONResponse(content=trust_engine.trust_report(top_n=top_n))


# Operational controls -------------------------------------------------

@router.post("/controls/freeze/{capability_id}")
async def controls_freeze_api(capability_id: str, request: Request):
    identity = _identity(request)
    enforcement.enforce(identity, "controls.manage")
    try:
        body = await request.json()
    except Exception:
        body = {}
    ctrl = controls.freeze(capability_id, actor=identity.as_actor(),
                             reason=body.get("reason", "operator freeze"))
    return JSONResponse(content=ctrl.to_dict())


@router.post("/controls/unfreeze/{capability_id}")
async def controls_unfreeze_api(capability_id: str, request: Request):
    identity = _identity(request)
    enforcement.enforce(identity, "controls.manage")
    try:
        body = await request.json()
    except Exception:
        body = {}
    removed = controls.unfreeze(capability_id, actor=identity.as_actor(),
                                  reason=body.get("reason", "operator unfreeze"))
    return JSONResponse(content={"removed": removed})


@router.post("/controls/quarantine/{capability_id}")
async def controls_quarantine_api(capability_id: str, request: Request):
    identity = _identity(request)
    enforcement.enforce(identity, "controls.manage")
    try:
        body = await request.json()
    except Exception:
        body = {}
    ctrl = controls.quarantine(capability_id, actor=identity.as_actor(),
                                  reason=body.get("reason", "operator quarantine"))
    return JSONResponse(content=ctrl.to_dict())


@router.post("/controls/emergency-rollback/{capability_id}")
async def controls_emergency_rollback_api(capability_id: str, request: Request):
    identity = _identity(request)
    enforcement.enforce(identity, "controls.manage")
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    target = body.get("target_version_id")
    if not target:
        return _ops_error(invalid_input("target_version_id is required"))
    result = controls.emergency_rollback(capability_id, target_version_id=target,
                                            actor=identity.as_actor(),
                                            reason=body.get("reason", "emergency rollback"))
    return JSONResponse(content=result)


@router.get("/controls/active")
async def controls_active_api(target_type: str | None = None):
    return JSONResponse(content=[c.to_dict() for c in controls.list_active(target_type=target_type)])


# ── Phase 6 routes ───────────────────────────────────────────────────


# Phase 6A: distributed coordination
@router.get("/runtime/locks")
async def runtime_locks_api():
    return JSONResponse(content=distributed_lock.list_active())


@router.get("/runtime/workers")
async def runtime_workers_api():
    return JSONResponse(content=[w.to_dict() for w in worker_coordination.list_workers()])


@router.post("/runtime/workers/evict-stale")
async def runtime_workers_evict_api():
    evicted = worker_coordination.evict_stale()
    return JSONResponse(content={"evicted": evicted})


@router.get("/runtime/queue")
async def runtime_queue_api(queue: str = "default", status: str | None = None,
                              limit: int = 200):
    jobs = runtime_queue.list_jobs(queue=queue, status=status, limit=limit)
    return JSONResponse(content=[j.to_dict() for j in jobs])


@router.get("/runtime/queue/depth")
async def runtime_queue_depth_api(queue: str = "default"):
    return JSONResponse(content=runtime_queue.queue_depth(queue=queue))


@router.post("/runtime/queue/enqueue")
async def runtime_queue_enqueue_api(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    capability_id = body.get("capability_id")
    if not capability_id:
        return _ops_error(invalid_input("capability_id is required"))
    job = runtime_queue.enqueue(
        kind="workflow_run", queue=body.get("queue", "default"),
        priority=int(body.get("priority", 0)),
        delay_seconds=int(body.get("delay_seconds", 0)),
        payload={"capability_id": capability_id, "inputs": body.get("inputs") or {}},
        correlation_id=body.get("correlation_id"),
        idempotency_key=body.get("idempotency_key"),
        enqueued_by=body.get("enqueued_by"),
    )
    return JSONResponse(content=job.to_dict())


@router.get("/runtime/queue/{job_id}/status")
async def runtime_queue_status_api(job_id: str):
    status = runtime_queue.status(job_id)
    if status is None:
        return _ops_error(not_found("job", job_id))
    return JSONResponse(content=status)


@router.post("/runtime/queue/{job_id}/cancel")
async def runtime_queue_cancel_api(job_id: str, request: Request):
    identity = _identity(request)
    ok = runtime_queue.cancel(job_id, actor=identity.as_actor())
    return JSONResponse(content={"cancelled": ok})


@router.post("/runtime/queue/sweep")
async def runtime_queue_sweep_api():
    reclaimed = runtime_queue.reclaim_stale()
    return JSONResponse(content={"reclaimed": reclaimed})


# Phase 6B: enterprise identity + policy + service identities
@router.get("/security/policies")
async def security_policies_api():
    return JSONResponse(content=policy_engine.list_policies())


@router.post("/security/policies")
async def security_policies_upsert_api(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        return JSONResponse(content=policy_engine.upsert_policy(body))
    except ValueError as e:
        return _ops_error(invalid_input(str(e)))


@router.post("/security/policies/{policy_id}/delete")
async def security_policies_delete_api(policy_id: str):
    ok = policy_engine.delete_policy(policy_id)
    return JSONResponse(content={"deleted": ok})


@router.get("/security/service-identities")
async def service_identities_list_api():
    return JSONResponse(content=[si.to_dict() for si in service_identities.list_all()])


@router.post("/security/service-identities")
async def service_identities_create_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        si, token = service_identities.create(
            display_name=body["display_name"],
            roles=body.get("roles") or ["viewer"],
            workspace_ids=body.get("workspace_ids") or [],
            description=body.get("description", ""),
            created_by=identity.as_actor(),
        )
    except KeyError as e:
        return _ops_error(invalid_input(f"missing field {e}"))
    return JSONResponse(content={"service_identity": si.to_dict(), "token": token})


@router.get("/security/posture")
async def security_posture_api(days: int = 7):
    return JSONResponse(content=security_telemetry.posture(days=days))


@router.get("/security/events")
async def security_events_api(days: int = 7):
    return JSONResponse(content={
        "failed_auth_attempts": security_telemetry.failed_auth_attempts(days=days),
        "repeated_denials": security_telemetry.repeated_denials(days=days),
        "service_identity_activity": security_telemetry.service_identity_activity(days=days),
    })


# Phase 6C: reliability + self-healing + incidents
@router.get("/reliability/findings")
async def reliability_findings_api():
    return JSONResponse(content=[f.to_dict() for f in reliability_monitor.scan()])


@router.post("/reliability/self-heal")
async def reliability_self_heal_api(request: Request):
    identity = _identity(request)
    enforcement.enforce(identity, "controls.manage")
    actions = self_healing.run_once(actor=identity.as_actor())
    return JSONResponse(content=[a.to_dict() for a in actions])


@router.get("/incidents")
async def incidents_list_api(state: str | None = None):
    return JSONResponse(content=[i.to_dict() for i in incidents.list_incidents(state=state)])


@router.get("/incidents/{incident_id}")
async def incidents_get_api(incident_id: str):
    inc = incidents.get(incident_id)
    if inc is None:
        return _ops_error(not_found("incident", incident_id))
    return JSONResponse(content=inc.to_dict())


@router.post("/incidents")
async def incidents_open_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    inc = incidents.open_incident(
        title=body.get("title", "Manual incident"),
        severity=int(body.get("severity", 3)),
        detector=body.get("detector", "manual"),
        impacted_capabilities=body.get("impacted_capabilities") or [],
        initial_note=body.get("initial_note", ""),
        actor=identity.as_actor(),
    )
    return JSONResponse(content=inc.to_dict())


@router.post("/incidents/{incident_id}/transition")
async def incidents_transition_api(incident_id: str, request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    to_state = body.get("to_state")
    if not to_state:
        return _ops_error(invalid_input("to_state is required"))
    try:
        inc = incidents.transition(incident_id, to_state=to_state,
                                     actor=identity.as_actor(),
                                     note=body.get("note", ""))
    except ValueError as e:
        return _ops_error(invalid_input(str(e)))
    if inc is None:
        return _ops_error(not_found("incident", incident_id))
    return JSONResponse(content=inc.to_dict())


@router.post("/incidents/{incident_id}/postmortem")
async def incidents_postmortem_api(incident_id: str, request: Request):
    identity = _identity(request)
    inc = incidents.draft_postmortem(incident_id, actor=identity.as_actor())
    if inc is None:
        return _ops_error(not_found("incident", incident_id))
    return JSONResponse(content=inc.to_dict())


# Phase 6D: scheduler
@router.get("/scheduler")
async def scheduler_list_api(enabled: bool | None = None):
    return JSONResponse(content=[s.to_dict() for s in scheduler.list_schedules(enabled=enabled)])


@router.post("/scheduler")
async def scheduler_create_api(request: Request):
    identity = _identity(request)
    enforcement.enforce(identity, "controls.manage")
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        s = scheduler.create_schedule(
            name=body["name"],
            trigger_kind=body["trigger_kind"],
            capability_id=body.get("capability_id"),
            payload=body.get("payload") or {},
            queue=body.get("queue", "default"),
            cron_expression=body.get("cron_expression"),
            interval_seconds=body.get("interval_seconds"),
            event_topic=body.get("event_topic"),
            blackout_windows=body.get("blackout_windows") or [],
            missed_runs_policy=body.get("missed_runs_policy", "fire_one"),
            created_by=identity.as_actor(),
            workspace_id=body.get("workspace_id"),
        )
    except (KeyError, ValueError) as e:
        return _ops_error(invalid_input(str(e)))
    return JSONResponse(content=s.to_dict())


@router.post("/scheduler/{schedule_id}/enable")
async def scheduler_enable_api(schedule_id: str, request: Request):
    identity = _identity(request)
    ok = scheduler.enable(schedule_id, actor=identity.as_actor())
    return JSONResponse(content={"enabled": ok})


@router.post("/scheduler/{schedule_id}/disable")
async def scheduler_disable_api(schedule_id: str, request: Request):
    identity = _identity(request)
    ok = scheduler.disable(schedule_id, actor=identity.as_actor())
    return JSONResponse(content={"disabled": ok})


@router.post("/scheduler/event/{event_topic}")
async def scheduler_event_api(event_topic: str, request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    enqueued = scheduler.fire_event(event_topic, payload=body.get("payload"),
                                       actor=identity.as_actor())
    return JSONResponse(content={"enqueued_jobs": enqueued})


@router.post("/scheduler/tick")
async def scheduler_tick_api():
    return JSONResponse(content=scheduler.tick())


# Phase 6E: approvals
@router.get("/approvals")
async def approvals_list_api(state: str | None = None, entity_id: str | None = None):
    rows = approvals.list_requests(state=state, entity_id=entity_id)
    return JSONResponse(content=[r.to_dict() for r in rows])


@router.post("/approvals")
async def approvals_request_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        req = approvals.request_approval(
            action=body["action"],
            entity_type=body["entity_type"],
            entity_id=body["entity_id"],
            requested_by=identity.as_actor(),
            stages=body.get("stages"),
            single_approver_roles=body.get("single_approver_roles") or ["admin"],
            quorum=int(body.get("quorum", 1)),
            reason=body.get("reason", ""),
            metadata=body.get("metadata") or {},
            ttl_hours=int(body.get("ttl_hours", 24)),
            correlation_id=body.get("correlation_id"),
        )
    except (KeyError, ValueError) as e:
        return _ops_error(invalid_input(str(e)))
    return JSONResponse(content=req.to_dict())


@router.post("/approvals/{request_id}/decide")
async def approvals_decide_api(request_id: str, request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    decision = body.get("decision")
    if decision not in ("approved", "rejected"):
        return _ops_error(invalid_input("decision must be 'approved' or 'rejected'"))
    try:
        approver_dict = identity.as_actor()
        approver_dict["roles"] = identity.roles
        req = approvals.submit_decision(request_id, approver=approver_dict,
                                           decision=decision,
                                           comment=body.get("comment", ""))
    except ValueError as e:
        return _ops_error(invalid_input(str(e)))
    if req is None:
        return _ops_error(not_found("approval_request", request_id))
    return JSONResponse(content=req.to_dict())


@router.post("/approvals/{request_id}/cancel")
async def approvals_cancel_api(request_id: str, request: Request):
    identity = _identity(request)
    req = approvals.cancel(request_id, actor=identity.as_actor())
    if req is None:
        return _ops_error(not_found("approval_request", request_id))
    return JSONResponse(content=req.to_dict())


# Phase 6F: experiments + evaluation
@router.get("/experiments")
async def experiments_list_api(state: str | None = None):
    return JSONResponse(content=[e.to_dict() for e in experiments.list_experiments(state=state)])


@router.post("/experiments")
async def experiments_create_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        exp = experiments.create_experiment(
            name=body["name"],
            capability_id=body["capability_id"],
            arms=body["arms"],
            sticky_session=bool(body.get("sticky_session", True)),
            description=body.get("description", ""),
            workspace_id=body.get("workspace_id"),
            created_by=identity.as_actor(),
        )
    except (KeyError, ValueError) as e:
        return _ops_error(invalid_input(str(e)))
    return JSONResponse(content=exp.to_dict())


@router.post("/experiments/{experiment_id}/transition")
async def experiments_transition_api(experiment_id: str, request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    to_state = body.get("to_state")
    if not to_state:
        return _ops_error(invalid_input("to_state is required"))
    try:
        exp = experiments.transition(experiment_id, to_state=to_state,
                                       actor=identity.as_actor())
    except ValueError as e:
        return _ops_error(invalid_input(str(e)))
    if exp is None:
        return _ops_error(not_found("experiment", experiment_id))
    return JSONResponse(content=exp.to_dict())


@router.get("/experiments/{experiment_id}/assign")
async def experiments_assign_api(experiment_id: str, session_id: str = ""):
    return JSONResponse(content=experiments.assign(experiment_id, session_id=session_id))


@router.get("/experiments/{experiment_id}/evaluate")
async def experiments_evaluate_api(experiment_id: str):
    return JSONResponse(content=evaluation.evaluate_experiment(experiment_id))


@router.get("/versions/{capability_id}/{version_id}/scorecard")
async def version_scorecard_api(capability_id: str, version_id: str):
    return JSONResponse(content=evaluation.version_scorecard(capability_id, version_id))


# ── Phase 7 routes ───────────────────────────────────────────────────


# Phase 7A: realtime bus, SSE, presence
@router.get("/realtime/events")
async def realtime_replay_api(since_sequence: int = 0, workspace_id: str | None = None,
                                event_types: str | None = None, limit: int = 200):
    types = [t.strip() for t in (event_types or "").split(",") if t.strip()] or None
    events = realtime_bus.replay(since_sequence=since_sequence,
                                    workspace_id=workspace_id,
                                    event_types=types, limit=limit)
    return JSONResponse(content=[e.to_dict() for e in events])


@router.get("/realtime/sse")
async def realtime_sse(request: Request, workspace_id: str | None = None,
                        event_types: str | None = None):
    types = [t.strip() for t in (event_types or "").split(",") if t.strip()] or None
    last_event_id = request.headers.get("Last-Event-ID", "0")
    try:
        since = int(last_event_id)
    except ValueError:
        since = 0
    generator = realtime_bus.stream(workspace_id=workspace_id,
                                       event_types=types,
                                       since_sequence=since)
    return StreamingResponse(generator, media_type="text/event-stream",
                               headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})


@router.post("/presence/heartbeat")
async def presence_heartbeat_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    workspace_id = body.get("workspace_id")
    if not workspace_id:
        return _ops_error(invalid_input("workspace_id is required"))
    entry = presence.heartbeat(
        workspace_id=workspace_id, identity=identity,
        currently_viewing=body.get("currently_viewing"),
        currently_editing=body.get("currently_editing"),
        typing_in=body.get("typing_in"),
    )
    if entry is None:
        return _ops_error(invalid_input("anonymous identity cannot heartbeat presence"))
    return JSONResponse(content=entry.to_dict())


@router.post("/presence/leave")
async def presence_leave_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    workspace_id = body.get("workspace_id")
    if not workspace_id:
        return _ops_error(invalid_input("workspace_id is required"))
    removed = presence.leave(workspace_id=workspace_id, identity=identity)
    return JSONResponse(content={"removed": removed})


@router.get("/presence/{workspace_id}")
async def presence_active_api(workspace_id: str):
    return JSONResponse(content=presence.active_in_workspace(workspace_id))


@router.get("/presence")
async def presence_all_api(request: Request):
    identity = _identity(request)
    if "admin" not in identity.roles:
        return _ops_error(OpsError(code="ACCESS_DENIED",
                                       message="admin role required to inspect global presence",
                                       status_code=403))
    return JSONResponse(content=presence.all_active())


# Phase 7B: copilot
@router.get("/copilot/ask")
async def copilot_ask_api(question: str, capability_id: str | None = None,
                            workspace_id: str | None = None, days: int = 7):
    answer = copilot.ask(question, capability_id=capability_id,
                            workspace_id=workspace_id, days=days)
    return JSONResponse(content=answer.to_dict())


@router.get("/copilot/recommendations")
async def copilot_recommendations_api():
    recs = copilot.operational_recommendations()
    return JSONResponse(content=[r.to_dict() for r in recs])


@router.get("/copilot/page", response_class=HTMLResponse)
async def copilot_page(request: Request, question: str = "",
                          capability_id: str | None = None):
    answer = None
    if question:
        result = copilot.ask(question, capability_id=capability_id)
        answer = result.to_dict()
    recs = [r.to_dict() for r in copilot.operational_recommendations()]
    return request.app.state.templates.TemplateResponse(
        request, "ops/copilot.html",
        {"question": question, "capability_id": capability_id,
         "answer": answer, "recommendations": recs},
    )


# Phase 7C: Redis activation status
@router.get("/runtime/redis/status")
async def runtime_redis_status_api():
    return JSONResponse(content={
        "redis_py_installed": redis_backends.is_available(),
        "client_wired": redis_backends._CLIENT is not None,
        "explainer": (
            "When redis_py_installed is False, install with `pip install redis`. "
            "When client_wired is False, call redis_backends.activate(client) "
            "in your bootstrap to enable Redis-backed cache + pubsub. "
            "Until then the platform runs in single-host multi-process mode."
        ),
    })


# Phase 7D: observability — tracing, alerts, notifications, prometheus
@router.get("/metrics/prometheus", response_class=PlainTextResponse)
async def metrics_prometheus_api():
    return PlainTextResponse(content=prometheus_exporter.render(),
                                media_type="text/plain; version=0.0.4")


@router.get("/tracing/recent")
async def tracing_recent_api(days: int = 1, limit: int = 200):
    return JSONResponse(content=tracing.list_recent(days=days, limit=limit))


@router.get("/tracing/{trace_id}")
async def tracing_get_api(trace_id: str):
    return JSONResponse(content=tracing.trace_tree(trace_id))


@router.get("/alerts/rules")
async def alerts_rules_api():
    return JSONResponse(content=[r.to_dict() for r in alerts.list_rules()])


@router.post("/alerts/rules")
async def alerts_rules_upsert_api(request: Request):
    identity = _identity(request)
    enforcement.enforce(identity, "controls.manage")
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        rule = alerts.upsert_rule(
            rule_id=body["rule_id"], name=body["name"], metric=body["metric"],
            operator=body["operator"], threshold=float(body["threshold"]),
            severity=int(body.get("severity", 3)),
            description=body.get("description", ""),
            suppress_for_seconds=int(body.get("suppress_for_seconds", 300)),
            escalation_after_seconds=int(body.get("escalation_after_seconds", 600)),
            notify_channels=body.get("notify_channels") or [],
            enabled=bool(body.get("enabled", True)),
        )
    except (KeyError, ValueError) as e:
        return _ops_error(invalid_input(str(e)))
    return JSONResponse(content=rule.to_dict())


@router.get("/alerts/active")
async def alerts_active_api():
    return JSONResponse(content=[a.to_dict() for a in alerts.list_active()])


@router.post("/alerts/evaluate")
async def alerts_evaluate_api(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    metric_values = body.get("metric_values") or {}
    fired = alerts.evaluate_rules(metric_values=metric_values)
    return JSONResponse(content={"fired_count": len(fired),
                                    "fired": [a.to_dict() for a in fired]})


@router.post("/alerts/{alert_id}/acknowledge")
async def alerts_ack_api(alert_id: str, request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    a = alerts.acknowledge(alert_id, actor=identity.as_actor(),
                              reason=body.get("reason", ""))
    if a is None:
        return _ops_error(not_found("alert", alert_id))
    return JSONResponse(content=a.to_dict())


@router.post("/alerts/{alert_id}/resolve")
async def alerts_resolve_api(alert_id: str, request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    a = alerts.resolve(alert_id, actor=identity.as_actor(),
                          reason=body.get("reason", ""))
    if a is None:
        return _ops_error(not_found("alert", alert_id))
    return JSONResponse(content=a.to_dict())


@router.get("/notifications/channels")
async def notif_channels_api():
    return JSONResponse(content=[c.to_dict() for c in notifications.list_channels()])


@router.post("/notifications/channels")
async def notif_channels_upsert_api(request: Request):
    identity = _identity(request)
    enforcement.enforce(identity, "controls.manage")
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        ch = notifications.upsert_channel(
            channel_id=body["channel_id"], name=body["name"],
            kind=body["kind"], config=body.get("config") or {},
            enabled=bool(body.get("enabled", True)),
        )
    except (KeyError, ValueError) as e:
        return _ops_error(invalid_input(str(e)))
    return JSONResponse(content=ch.to_dict())


@router.get("/notifications/history")
async def notif_history_api(days: int = 7):
    return JSONResponse(content=notifications.delivery_history(days=days))


# Phase 7E: change requests + compliance
@router.get("/change-requests")
async def change_requests_list_api(state: str | None = None):
    return JSONResponse(content=[c.to_dict() for c in change_requests.list_change_requests(state=state)])


@router.post("/change-requests")
async def change_requests_draft_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        cr = change_requests.draft(
            title=body["title"], action=body["action"],
            entity_type=body["entity_type"], entity_id=body["entity_id"],
            proposed_change=body.get("proposed_change") or {},
            rollback_plan=body.get("rollback_plan", ""),
            requested_by=identity.as_actor(),
            linked_incident_ids=body.get("linked_incident_ids") or [],
            linked_experiment_ids=body.get("linked_experiment_ids") or [],
            execution_window_start=body.get("execution_window_start"),
            execution_window_end=body.get("execution_window_end"),
            notes=body.get("notes", ""),
        )
    except KeyError as e:
        return _ops_error(invalid_input(f"missing field {e}"))
    return JSONResponse(content=cr.to_dict())


@router.post("/change-requests/{cr_id}/submit")
async def change_requests_submit_api(cr_id: str, request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    cr = change_requests.submit(
        cr_id, single_approver_roles=body.get("single_approver_roles") or ["admin"],
        quorum=int(body.get("quorum", 1)),
        ttl_hours=int(body.get("ttl_hours", 48)),
        actor=identity.as_actor(),
    )
    if cr is None:
        return _ops_error(not_found("change_request", cr_id))
    return JSONResponse(content=cr.to_dict())


@router.post("/change-requests/{cr_id}/sync")
async def change_requests_sync_api(cr_id: str):
    cr = change_requests.sync_state_from_approval(cr_id)
    if cr is None:
        return _ops_error(not_found("change_request", cr_id))
    return JSONResponse(content=cr.to_dict())


@router.post("/change-requests/{cr_id}/cancel")
async def change_requests_cancel_api(cr_id: str, request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    cr = change_requests.cancel(cr_id, actor=identity.as_actor(),
                                   reason=body.get("reason", ""))
    if cr is None:
        return _ops_error(not_found("change_request", cr_id))
    return JSONResponse(content=cr.to_dict())


@router.get("/compliance/report")
async def compliance_report_api(days: int = 30, format: str = "json"):
    try:
        out = compliance_reports.operational_report(days=days, format=format)
    except ValueError as e:
        return _ops_error(invalid_input(str(e)))
    if format == "json":
        return JSONResponse(content=out)
    return PlainTextResponse(content=out, media_type=("text/csv" if format == "csv" else "text/markdown"))


@router.get("/compliance/access-review")
async def compliance_access_review_api(days: int = 30):
    return JSONResponse(content=compliance_reports.access_review(days=days))


@router.get("/compliance/approval-timeline")
async def compliance_approval_timeline_api(days: int = 30):
    return JSONResponse(content=compliance_reports.approval_timeline(days=days))


@router.get("/compliance/routing-report")
async def compliance_routing_report_api(days: int = 7):
    return JSONResponse(content=compliance_reports.routing_decision_report(days=days))


@router.get("/compliance/audit-replay/{correlation_id}")
async def compliance_audit_replay_api(correlation_id: str):
    return JSONResponse(content=compliance_reports.audit_replay_export(correlation_id=correlation_id))


# Phase 7F: dashboard + replay UI
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    kpis = {
        "queue_depth": runtime_queue.queue_depth().get("total", 0),
        "workers_active": len([w for w in worker_coordination.list_workers() if w.status == "active"]),
        "incidents_open": len(incidents.list_incidents(state="open")),
        "alerts_open": len(alerts.list_active()),
        "approvals_pending": len(approvals.list_requests(state="pending")) +
                                len(approvals.list_requests(state="in_progress")),
        "experiments_running": len(experiments.list_experiments(state="running")),
    }
    presence_all = presence.all_active() or {}
    return request.app.state.templates.TemplateResponse(
        request, "ops/dashboard.html",
        {"kpis": kpis, "alerts": [a.to_dict() for a in alerts.list_active()[:10]],
         "presence": presence_all},
    )


@router.get("/replay/page", response_class=HTMLResponse)
async def replay_page(request: Request, correlation_id: str = ""):
    entries = []
    if correlation_id:
        entries = audit_log.replay(correlation_id, days=90)
    return request.app.state.templates.TemplateResponse(
        request, "ops/replay.html",
        {"correlation_id": correlation_id, "entries": entries},
    )


# ── Phase 8 routes ───────────────────────────────────────────────────


# 8A: collab + WS gateway
@router.get("/collab/sessions")
async def collab_sessions_list_api(entity_type: str | None = None,
                                       entity_id: str | None = None):
    sessions = collab_sessions.list_sessions(entity_type=entity_type,
                                                entity_id=entity_id)
    return JSONResponse(content=[s.to_dict() for s in sessions])


@router.post("/collab/session")
async def collab_session_open_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        s = collab_sessions.open_session(
            entity_type=body["entity_type"],
            entity_id=body["entity_id"],
            editor=identity,
            intent=body.get("intent", "edit"),
            current_revision=body.get("current_revision"),
        )
    except KeyError as e:
        return _ops_error(invalid_input(f"missing {e}"))
    except PermissionError as e:
        return _ops_error(OpsError(code="ACCESS_DENIED", message=str(e), status_code=403))
    except collab_sessions.EditLockHeld as e:
        return _ops_error(conflict(str(e)))
    return JSONResponse(content=s.to_dict())


@router.post("/collab/session/{session_id}/close")
async def collab_session_close_api(session_id: str, request: Request):
    identity = _identity(request)
    ok = collab_sessions.close_session(session_id, editor=identity)
    return JSONResponse(content={"closed": ok})


@router.post("/collab/session/{session_id}/heartbeat")
async def collab_session_heartbeat_api(session_id: str, request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    s = collab_sessions.heartbeat_session(session_id, editor=identity,
                                              cursor_position=body.get("cursor_position"))
    if s is None:
        return _ops_error(not_found("collab_session", session_id))
    return JSONResponse(content=s.to_dict())


@router.post("/collab/comment")
async def collab_comment_post_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        cm = collab_sessions.post_comment(
            entity_type=body["entity_type"], entity_id=body["entity_id"],
            author=identity, body=body["body"], anchor=body.get("anchor"),
        )
    except KeyError as e:
        return _ops_error(invalid_input(f"missing {e}"))
    except (PermissionError, ValueError) as e:
        return _ops_error(invalid_input(str(e)))
    return JSONResponse(content=cm.to_dict())


@router.get("/collab/comments/{entity_id}")
async def collab_comments_list_api(entity_id: str, include_resolved: bool = False):
    out = collab_sessions.list_comments(entity_id, include_resolved=include_resolved)
    return JSONResponse(content=[c.to_dict() for c in out])


@router.get("/collab/revisions/{entity_id}")
async def collab_revisions_list_api(entity_id: str, limit: int = 100):
    revs = collab_sessions.list_revisions(entity_id, limit=limit)
    return JSONResponse(content=[r.to_dict() for r in revs])


@router.get("/realtime/ws/mode")
async def realtime_ws_mode_api():
    return JSONResponse(content=ws_gateway.mode())


@router.websocket("/realtime/ws")
async def realtime_ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    sub_id, q, notify = realtime_bus.subscribe()
    try:
        # Replay initial events
        events = realtime_bus.replay(limit=50)
        for e in events:
            await websocket.send_json(e.to_dict())
        # Live loop
        while True:
            triggered = notify.wait(timeout=15)
            if triggered:
                notify.clear()
                while q:
                    e = q.popleft()
                    await websocket.send_json(e.to_dict())
            else:
                # heartbeat
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.warning("ws stream raised", exc_info=True)
    finally:
        realtime_bus.unsubscribe(sub_id)


# 8B: agents
@router.get("/agents")
async def agents_list_api(only_active: bool = False):
    return JSONResponse(content=[a.to_dict()
                                    for a in agent_registry.list_agents(only_active=only_active)])


@router.post("/agents")
async def agents_register_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        agent = agent_registry.register_agent(
            name=body["name"], description=body.get("description", ""),
            autonomy_policy=body["autonomy_policy"],
            confidence_threshold=float(body["confidence_threshold"]),
            permitted_actions=body.get("permitted_actions") or [],
            scope=body.get("scope") or {},
            rollback_required=bool(body.get("rollback_required", True)),
            created_by=identity.as_actor(),
        )
    except (KeyError, ValueError) as e:
        return _ops_error(invalid_input(str(e)))
    return JSONResponse(content=agent.to_dict())


@router.post("/agents/{agent_id}/execute")
async def agents_execute_api(agent_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        ex = agent_runtime.execute(
            agent_id=agent_id,
            action_kind=body["action_kind"],
            target=body.get("target") or {},
            inputs=body.get("inputs") or {},
            reasoning_chain=body.get("reasoning_chain") or [],
            evidence_refs=body.get("evidence_refs") or [],
            confidence=float(body.get("confidence", 0.0)),
            rollback_plan=body.get("rollback_plan", ""),
            risk=body.get("risk", "medium"),
        )
    except (KeyError, agent_runtime.AutonomyViolation, ValueError) as e:
        return _ops_error(invalid_input(str(e)))
    return JSONResponse(content=ex.to_dict())


@router.post("/agents/{agent_id}/pause")
async def agents_pause_api(agent_id: str, request: Request):
    identity = _identity(request)
    a = agent_registry.pause(agent_id, actor=identity.as_actor())
    if a is None:
        return _ops_error(not_found("agent", agent_id))
    return JSONResponse(content=a.to_dict())


@router.post("/agents/{agent_id}/resume")
async def agents_resume_api(agent_id: str, request: Request):
    identity = _identity(request)
    a = agent_registry.resume(agent_id, actor=identity.as_actor())
    if a is None:
        return _ops_error(not_found("agent", agent_id))
    return JSONResponse(content=a.to_dict())


@router.get("/agents/{agent_id}/executions")
async def agents_executions_api(agent_id: str, outcome: str | None = None,
                                   limit: int = 100):
    rows = agent_runtime.list_executions(agent_id=agent_id, outcome=outcome,
                                            limit=limit)
    return JSONResponse(content=[r.to_dict() for r in rows])


@router.post("/agents/executions/{execution_id}/revoke")
async def agents_execution_revoke_api(execution_id: str, request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ex = agent_runtime.revoke(execution_id, actor=identity.as_actor(),
                                 reason=body.get("reason", "operator revoke"))
    if ex is None:
        return _ops_error(not_found("agent_execution", execution_id))
    return JSONResponse(content=ex.to_dict())


# 8C: knowledge graph
@router.get("/graph/entity/{kind}/{node_id}")
async def graph_entity_api(kind: str, node_id: str):
    full_id = f"{kind}:{node_id}"
    return JSONResponse(content=knowledge_graph.related(full_id, max_depth=1))


@router.get("/graph/related/{kind}/{node_id}")
async def graph_related_api(kind: str, node_id: str, max_depth: int = 2):
    full_id = f"{kind}:{node_id}"
    return JSONResponse(content=knowledge_graph.related(full_id, max_depth=max_depth))


@router.get("/graph/causal-replay/{incident_id}")
async def graph_causal_replay_api(incident_id: str):
    return JSONResponse(content=knowledge_graph.causal_replay(incident_id=incident_id))


# 8D: chaos
@router.post("/chaos/inject")
async def chaos_inject_api(request: Request):
    identity = _identity(request)
    enforcement.enforce(identity, "controls.manage")
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        inj = chaos_engine.inject(
            kind=body["kind"], target_id=body.get("target_id", ""),
            duration_seconds=int(body.get("duration_seconds", 60)),
            actor=identity.as_actor(),
            reason=body.get("reason", "scheduled chaos drill"),
        )
    except (KeyError, ValueError) as e:
        return _ops_error(invalid_input(str(e)))
    return JSONResponse(content=inj.to_dict())


@router.post("/chaos/{injection_id}/revert")
async def chaos_revert_api(injection_id: str, request: Request):
    identity = _identity(request)
    inj = chaos_engine.revert(injection_id, actor=identity.as_actor())
    if inj is None:
        return _ops_error(not_found("chaos_injection", injection_id))
    return JSONResponse(content=inj.to_dict())


@router.get("/chaos/history")
async def chaos_history_api(state: str | None = None):
    return JSONResponse(content=[i.to_dict()
                                    for i in chaos_engine.list_injections(state=state)])


@router.get("/chaos/mttr")
async def chaos_mttr_api():
    return JSONResponse(content=chaos_engine.measure_mttr())


# 8E: orchestrations
@router.post("/orchestrations")
async def orch_create_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    try:
        o = orchestration_engine.create_orchestration(
            name=body["name"], steps=body["steps"],
            initiated_by=identity.as_actor(),
            context=body.get("context") or {},
        )
    except (KeyError, ValueError) as e:
        return _ops_error(invalid_input(str(e)))
    return JSONResponse(content=o.to_dict())


@router.get("/orchestrations")
async def orch_list_api(state: str | None = None, limit: int = 50):
    rows = orchestration_engine.list_orchestrations(state=state, limit=limit)
    return JSONResponse(content=[o.to_dict() for o in rows])


@router.get("/orchestrations/{orchestration_id}")
async def orch_get_api(orchestration_id: str):
    o = orchestration_engine.get(orchestration_id)
    if o is None:
        return _ops_error(not_found("orchestration", orchestration_id))
    return JSONResponse(content=o.to_dict())


@router.post("/orchestrations/{orchestration_id}/pause")
async def orch_pause_api(orchestration_id: str, request: Request):
    identity = _identity(request)
    o = orchestration_engine.pause(orchestration_id, actor=identity.as_actor())
    if o is None:
        return _ops_error(not_found("orchestration", orchestration_id))
    return JSONResponse(content=o.to_dict())


@router.post("/orchestrations/{orchestration_id}/resume")
async def orch_resume_api(orchestration_id: str, request: Request):
    identity = _identity(request)
    o = orchestration_engine.resume(orchestration_id, actor=identity.as_actor())
    if o is None:
        return _ops_error(not_found("orchestration", orchestration_id))
    return JSONResponse(content=o.to_dict())


@router.post("/orchestrations/{orchestration_id}/rewind")
async def orch_rewind_api(orchestration_id: str, request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    o = orchestration_engine.rewind(orchestration_id,
                                       to_step_id=body.get("to_step_id", ""),
                                       actor=identity.as_actor())
    if o is None:
        return _ops_error(not_found("orchestration", orchestration_id))
    return JSONResponse(content=o.to_dict())


# 8F: governance hardening
@router.post("/audit/sign-pending")
async def audit_sign_pending_api(days_back: int = 1):
    n = signed_audit.sign_pending(days_back=days_back)
    return JSONResponse(content={"signed_count": n,
                                    "signing_mode": signed_audit.signing_mode()})


@router.post("/audit/verify")
async def audit_verify_api(days: int = 30):
    return JSONResponse(content=signed_audit.verify_chain(days=days).to_dict())


@router.post("/retention/apply")
async def retention_apply_api():
    out = retention_policy.apply_policy()
    return JSONResponse(content=[r.to_dict() for r in out])


@router.get("/retention/policy")
async def retention_policy_api():
    return JSONResponse(content=retention_policy.list_policy())


@router.post("/access-reviews/run")
async def access_reviews_run_api(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    campaign = access_reviews.run_campaign(
        name=body.get("name", "quarterly_access_review"),
        lookback_days=int(body.get("lookback_days", 90)),
        inactivity_days=int(body.get("inactivity_days", 30)),
    )
    return JSONResponse(content=campaign.to_dict())


@router.get("/access-reviews")
async def access_reviews_list_api():
    return JSONResponse(content=[c.to_dict() for c in access_reviews.list_campaigns()])


@router.get("/governance/scorecards")
async def governance_scorecards_api(workspace_id: str | None = None,
                                       lookback_days: int = 30):
    if workspace_id:
        return JSONResponse(content=governance_scorecards.build(
            workspace_id=workspace_id, lookback_days=lookback_days,
        ).to_dict())
    rows = governance_scorecards.build_all(lookback_days=lookback_days)
    return JSONResponse(content=[r.to_dict() for r in rows])


# 8G: forecasts
@router.get("/forecast/queue")
async def forecast_queue_api(horizon_minutes: int = 30):
    return JSONResponse(content=forecasting.forecast_queue_saturation(
        horizon_minutes=horizon_minutes,
    ).to_dict())


@router.get("/forecast/incidents")
async def forecast_incidents_api(horizon_hours: int = 6):
    return JSONResponse(content=forecasting.forecast_incident_probability(
        horizon_hours=horizon_hours,
    ).to_dict())


@router.get("/forecast/capacity")
async def forecast_capacity_api():
    recs = forecasting.capacity_recommendations()
    return JSONResponse(content=[r.to_dict() for r in recs])


@router.get("/forecast/drift")
async def forecast_drift_api(lookback_hours: int = 6):
    return JSONResponse(content={
        "routing_drift": forecasting.detect_routing_drift(lookback_hours=lookback_hours),
        "latency_drift": forecasting.detect_latency_drift(),
        "approval_bottlenecks": forecasting.detect_approval_bottlenecks(),
    })


# 8H: backup + migrations
@router.get("/system/backup")
async def system_backup_list_api():
    return JSONResponse(content=backup_restore.list_snapshots())


@router.post("/system/backup")
async def system_backup_create_api(request: Request):
    identity = _identity(request)
    result = backup_restore.snapshot(actor=identity.as_actor())
    return JSONResponse(content=result.to_dict())


@router.post("/system/restore")
async def system_restore_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    archive_path = body.get("archive_path")
    if not archive_path:
        return _ops_error(invalid_input("archive_path is required"))
    try:
        result = backup_restore.restore(archive_path=archive_path,
                                            restore_to=body.get("restore_to"),
                                            actor=identity.as_actor())
    except FileNotFoundError as e:
        return _ops_error(not_found("backup_archive", archive_path))
    return JSONResponse(content=result.to_dict())


@router.get("/system/migrations")
async def system_migrations_status_api():
    return JSONResponse(content=migrations.status())


@router.post("/system/migrations/apply")
async def system_migrations_apply_api():
    return JSONResponse(content={"results": migrations.apply_pending()})


@router.post("/system/migrations/rollback")
async def system_migrations_rollback_api():
    return JSONResponse(content=migrations.rollback_one())


# ── Phase 9 routes ───────────────────────────────────────────────────


# 9A: event_fabric
@router.get("/fabric/events")
async def fabric_events_api(since_sequence: int = 0,
                                workspace_id: str | None = None,
                                event_types: str | None = None,
                                correlation_id: str | None = None,
                                limit: int = 200):
    types = [t.strip() for t in (event_types or "").split(",") if t.strip()] or None
    events = event_fabric.replay(since_sequence=since_sequence,
                                     workspace_id=workspace_id,
                                     event_types=types,
                                     correlation_id=correlation_id,
                                     limit=limit)
    return JSONResponse(content=[e.to_dict() for e in events])


@router.get("/fabric/consistency-report")
async def fabric_consistency_api():
    return JSONResponse(content=event_fabric.consistency_report())


@router.get("/fabric/sse")
async def fabric_sse(request: Request, workspace_id: str | None = None,
                        event_types: str | None = None):
    types = [t.strip() for t in (event_types or "").split(",") if t.strip()] or None
    last_event_id = request.headers.get("Last-Event-ID", "0")
    try:
        since = int(last_event_id)
    except ValueError:
        since = 0
    generator = event_fabric.stream(workspace_id=workspace_id,
                                       event_types=types,
                                       since_sequence=since)
    return StreamingResponse(generator, media_type="text/event-stream",
                               headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})


# 9B/C/D: distributed coordination diagnostics
@router.get("/coordination/topology")
async def coordination_topology_api():
    return JSONResponse(content=coordination_diagnostics.coordination_topology())


@router.get("/coordination/locks")
async def coordination_locks_api(names: str | None = None):
    lock_list = [n.strip() for n in (names or "").split(",") if n.strip()] or None
    return JSONResponse(content=coordination_diagnostics.lock_inspector(lock_names=lock_list))


@router.get("/coordination/stream-lag")
async def coordination_stream_lag_api(group: str = "ops_consumer",
                                          event_types: str | None = None):
    types = [t.strip() for t in (event_types or "").split(",") if t.strip()] or None
    return JSONResponse(content=coordination_diagnostics.stream_lag_report(
        group=group, event_types=types,
    ))


@router.get("/coordination/replay-backlog")
async def coordination_backlog_api():
    return JSONResponse(content=coordination_diagnostics.replay_backlog())


@router.get("/coordination/orphan-orchestrations")
async def coordination_orphan_api(age_minutes: int = 30):
    return JSONResponse(content=coordination_diagnostics.orphan_orchestrations(age_minutes=age_minutes))


@router.get("/system/cluster-health")
async def cluster_health_api():
    return JSONResponse(content=coordination_diagnostics.cluster_health())


# 9C: WebSocket topology
@router.get("/coordination/ws-topology")
async def ws_topology_api():
    try:
        return JSONResponse(content=distributed_presence.ws_topology())
    except Exception as e:
        return _ops_error(invalid_input(f"ws topology unavailable: {e}"))


# 9D: orchestration runtime claims
@router.get("/coordination/orchestration-mode")
async def orchestration_mode_api():
    return JSONResponse(content=orchestration_runtime.coordination_mode())


@router.get("/coordination/active-claims")
async def active_claims_api():
    claims = orchestration_runtime.list_active_claims()
    return JSONResponse(content=[c.to_dict() for c in claims])


@router.post("/coordination/reclaim-expired")
async def reclaim_expired_api():
    expired = orchestration_runtime.reclaim_expired()
    return JSONResponse(content={"reclaimed": [c.to_dict() for c in expired]})


# 9E: projections
@router.get("/projections")
async def projections_list_api():
    projection_engine.register_default_projections()
    return JSONResponse(content=projection_engine.list_projections())


@router.post("/projections/{name}/rebuild")
async def projection_rebuild_api(name: str, from_sequence: int = 0):
    projection_engine.register_default_projections()
    try:
        return JSONResponse(content=projection_engine.rebuild(name, from_sequence=from_sequence))
    except KeyError:
        return _ops_error(not_found("projection", name))


@router.get("/projections/{name}/latest")
async def projection_latest_api(name: str):
    out = projection_engine.latest(name)
    if out is None:
        return _ops_error(not_found("projection", name))
    return JSONResponse(content=out)


@router.get("/projections/{name}/verify")
async def projection_verify_api(name: str):
    projection_engine.register_default_projections()
    try:
        return JSONResponse(content=projection_engine.compare_with_latest(name))
    except KeyError:
        return _ops_error(not_found("projection", name))


# 9B: Redis-backed lock v2 inspector (read-only)
@router.get("/coordination/redis-lock/{lock_name}")
async def redis_lock_inspect_api(lock_name: str):
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        return JSONResponse(content={"scope": "no-redis",
                                          "explainer": "wire redis_backends.activate(client) to use distributed_lock_v2"})
    try:
        return JSONResponse(content=distributed_lock_v2.is_held(lock_name) or
                                          {"lock_name": lock_name, "held": False})
    except Exception as e:
        return _ops_error(invalid_input(f"redis lock inspect failed: {e}"))


# ── Phase 10 routes ──────────────────────────────────────────────────


# 10A: transactional outbox
@router.get("/outbox/metrics")
async def outbox_metrics_api():
    return JSONResponse(content=transactional_outbox.metrics())


@router.get("/outbox/entries")
async def outbox_entries_api(state: str | None = None, limit: int = 100):
    entries = transactional_outbox.list_entries(state=state, limit=limit)
    return JSONResponse(content=[e.to_dict() for e in entries])


@router.get("/outbox/dlq")
async def outbox_dlq_api(limit: int = 100):
    return JSONResponse(content=[e.to_dict()
                                    for e in transactional_outbox.list_dlq(limit=limit)])


@router.post("/outbox/enqueue")
async def outbox_enqueue_api(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    entry = transactional_outbox.enqueue(
        event_type=body.get("event_type", "outbox.test"),
        payload=body.get("payload") or {},
        target=body.get("target", "fabric"),
        idempotency_key=body.get("idempotency_key"),
        correlation_id=body.get("correlation_id"),
        max_attempts=int(body.get("max_attempts", 5)),
    )
    return JSONResponse(content=entry.to_dict())


@router.post("/outbox/drain")
async def outbox_drain_api(max_batch: int = 25):
    return JSONResponse(content=transactional_outbox.drain_once(max_batch=max_batch))


@router.post("/outbox/reconcile")
async def outbox_reconcile_api():
    return JSONResponse(content=transactional_outbox.reconcile_after_outage())


@router.post("/outbox/dlq/{outbox_id}/replay")
async def outbox_dlq_replay_api(outbox_id: str, request: Request):
    identity = _identity(request)
    entry = transactional_outbox.replay_dlq(outbox_id, actor=identity.as_actor())
    if entry is None:
        return _ops_error(not_found("outbox_entry", outbox_id))
    return JSONResponse(content=entry.to_dict())


# 10B: Redis Sentinel + failover
@router.get("/redis/state")
async def redis_state_api():
    state = redis_sentinel.current_state()
    state["client_wired"] = redis_backends._CLIENT is not None
    return JSONResponse(content=state)


@router.post("/redis/check-failover")
async def redis_check_failover_api():
    return JSONResponse(content=redis_sentinel.check_failover())


@router.get("/redis/warnings")
async def redis_warnings_api():
    return JSONResponse(content={"warnings": redis_sentinel.cluster_warnings()})


# 10C: poison handling
@router.get("/poison/quarantine")
async def poison_list_api(days: int = 30, limit: int = 200):
    return JSONResponse(content=[r.to_dict()
                                    for r in poison_handler.list_quarantine(days=days, limit=limit)])


@router.post("/poison/release/{quarantine_id}")
async def poison_release_api(quarantine_id: str, request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    record = poison_handler.release(quarantine_id, actor=identity.as_actor(),
                                        reason=body.get("reason", "operator release"))
    if record is None:
        return _ops_error(not_found("poison_record", quarantine_id))
    return JSONResponse(content=record.to_dict())


@router.post("/poison/replay-released")
async def poison_replay_released_api():
    return JSONResponse(content=poison_handler.replay_released())


# 10D: recovery coordinator
@router.get("/recovery/scan")
async def recovery_scan_api():
    recs = recovery_coordinator.scan()
    return JSONResponse(content=[r.to_dict() for r in recs])


@router.post("/recovery/execute-all")
async def recovery_execute_all_api(request: Request):
    identity = _identity(request)
    return JSONResponse(content=recovery_coordinator.execute_all_autoexecutable(
        actor=identity.as_actor(),
    ))


# 10E: backup integrity
@router.post("/backup/manifest")
async def backup_create_manifest_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    manifest = backup_integrity.snapshot_with_manifest(
        actor=identity.as_actor(),
        notes=body.get("notes", ""),
        parent_snapshot_id=body.get("parent_snapshot_id"),
    )
    return JSONResponse(content=manifest.to_dict())


@router.get("/backup/manifests")
async def backup_manifests_list_api():
    return JSONResponse(content=backup_integrity.list_manifests())


@router.post("/backup/verify/{manifest_id}")
async def backup_verify_api(manifest_id: str):
    return JSONResponse(content=backup_integrity.verify_snapshot(manifest_id))


@router.post("/backup/partial-restore")
async def backup_partial_restore_api(request: Request):
    identity = _identity(request)
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    result = backup_integrity.partial_restore(
        manifest_id=body.get("manifest_id", ""),
        profile=body.get("profile", ""),
        restore_to=body.get("restore_to"),
        actor=identity.as_actor(),
    )
    if not result.get("restored"):
        return _ops_error(invalid_input(result.get("reason", "restore failed"),
                                              details=result))
    return JSONResponse(content=result)


@router.get("/backup/lineage")
async def backup_lineage_api():
    return JSONResponse(content=backup_integrity.lineage_graph())


@router.get("/backup/orphans")
async def backup_orphans_api():
    return JSONResponse(content=backup_integrity.orphan_snapshots())


# 10F: orchestration recovery
@router.post("/orchestrations/{orchestration_id}/checkpoint")
async def orch_checkpoint_save_api(orchestration_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        return _ops_error(invalid_input("body must be JSON"))
    ckpt = orchestration_recovery.save_checkpoint(
        orchestration_id=orchestration_id,
        step_id=body.get("step_id", ""),
        payload=body.get("payload") or {},
    )
    return JSONResponse(content=ckpt.to_dict())


@router.get("/orchestrations/{orchestration_id}/checkpoint/{step_id}")
async def orch_checkpoint_load_api(orchestration_id: str, step_id: str):
    ckpt = orchestration_recovery.load_checkpoint(orchestration_id=orchestration_id,
                                                      step_id=step_id)
    if ckpt is None:
        return _ops_error(not_found("checkpoint", f"{orchestration_id}:{step_id}"))
    return JSONResponse(content=ckpt.to_dict())


@router.post("/orchestrations/{orchestration_id}/heartbeat")
async def orch_heartbeat_api(orchestration_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    orchestration_recovery.write_heartbeat(
        orchestration_id=orchestration_id,
        worker_id=body.get("worker_id", "anonymous"),
        step_id=body.get("step_id"),
    )
    return JSONResponse(content={"recorded": True})


@router.post("/orchestrations/recover-after-crash")
async def orch_recover_after_crash_api(age_minutes: int = 5):
    return JSONResponse(content=orchestration_recovery.recover_after_crash(age_minutes=age_minutes))


@router.get("/orchestrations/{orchestration_id}/timeline")
async def orch_timeline_api(orchestration_id: str):
    return JSONResponse(content=orchestration_recovery.operator_timeline(orchestration_id))


# 10G: load test harness
@router.post("/loadtest/run-suite")
async def loadtest_run_suite_api():
    suite = load_test.run_suite()
    return JSONResponse(content={"results": [b.to_dict() for b in suite]})


@router.get("/loadtest/history")
async def loadtest_history_api(limit: int = 10):
    return JSONResponse(content=load_test.list_suites(limit=limit))
