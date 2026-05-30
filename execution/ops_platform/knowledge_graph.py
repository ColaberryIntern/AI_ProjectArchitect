"""Knowledge graph — relationships between capabilities, incidents,
experiments, alerts, approvals, operators, workspaces, schedules, change
requests, agent executions.

Built on demand from existing structured sources; no new persistence beyond
the snapshot file. Edges are derived from concrete data — never invented.
Causal claims always cite source data via evidence_refs.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import (
    alerts, audit_log, capability_versions, change_requests, controls,
    experiments, incidents, scheduler, workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

_GRAPH_DIR = OUTPUT_DIR / "ops_platform" / "knowledge_graph"


@dataclass
class Node:
    node_id: str            # "{kind}:{id}"
    kind: str               # capability | incident | experiment | alert | approval | operator | workspace | schedule | change_request | agent_execution | capability_version
    label: str
    attributes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Edge:
    src: str                # node_id
    dst: str                # node_id
    kind: str               # touched_by | followed | impacted | derives_from | references | resolves | correlates_with
    evidence_refs: list = field(default_factory=list)
    weight: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class KnowledgeGraph:
    nodes: dict = field(default_factory=dict)
    edges: list = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "nodes": [n if isinstance(n, dict) else n.to_dict() for n in self.nodes.values()],
            "edges": [e if isinstance(e, dict) else e.to_dict() for e in self.edges],
            "generated_at": self.generated_at,
        }


# ── Public API ─────────────────────────────────────────────────────────


def build_graph(
    *,
    lookback_days: int = 30,
    registry: CapabilityRegistry | None = None,
    persist: bool = False,
) -> KnowledgeGraph:
    reg = registry or default_registry()
    g = KnowledgeGraph(generated_at=datetime.now(timezone.utc).isoformat())

    # ── Nodes ──
    for cap in reg.snapshot().capabilities:
        _add_node(g, kind="capability", node_id=cap["id"],
                    label=cap.get("name", cap["id"]),
                    attributes={"category": cap.get("category"),
                                "type_": cap.get("type")})
    for inc in incidents.list_incidents():
        _add_node(g, kind="incident", node_id=inc.incident_id,
                    label=inc.title,
                    attributes={"severity": inc.severity, "state": inc.state})
        for cid in inc.impacted_capabilities or []:
            _add_edge(g, src=f"incident:{inc.incident_id}", dst=f"capability:{cid}",
                        kind="impacted",
                        evidence_refs=[{"source": "incident",
                                         "incident_id": inc.incident_id}])

    for exp in experiments.list_experiments():
        _add_node(g, kind="experiment", node_id=exp.experiment_id,
                    label=exp.name,
                    attributes={"state": exp.state,
                                "capability_id": exp.capability_id})
        _add_edge(g, src=f"experiment:{exp.experiment_id}",
                    dst=f"capability:{exp.capability_id}", kind="touches",
                    evidence_refs=[{"source": "experiment",
                                     "experiment_id": exp.experiment_id}])

    for a in alerts.list_active():
        _add_node(g, kind="alert", node_id=a.alert_id, label=a.metric,
                    attributes={"severity": a.severity, "state": a.state})

    for cr in change_requests.list_change_requests():
        _add_node(g, kind="change_request", node_id=cr.cr_id, label=cr.title,
                    attributes={"state": cr.state, "action": cr.action})
        _add_edge(g, src=f"change_request:{cr.cr_id}",
                    dst=f"{cr.entity_type}:{cr.entity_id}",
                    kind="proposes_change_to",
                    evidence_refs=[{"source": "change_request", "cr_id": cr.cr_id}])

    for s in scheduler.list_schedules():
        _add_node(g, kind="schedule", node_id=s.schedule_id, label=s.name,
                    attributes={"trigger_kind": s.trigger_kind,
                                "enabled": s.enabled})
        if s.capability_id:
            _add_edge(g, src=f"schedule:{s.schedule_id}",
                        dst=f"capability:{s.capability_id}", kind="triggers",
                        evidence_refs=[{"source": "schedule",
                                         "schedule_id": s.schedule_id}])

    for cap in reg.snapshot().capabilities:
        for v in capability_versions.list_versions(cap["id"]):
            _add_node(g, kind="capability_version", node_id=v.version_id,
                        label=f"{cap['id']}@{v.semver}",
                        attributes={"status": v.status, "semver": v.semver})
            _add_edge(g, src=f"capability_version:{v.version_id}",
                        dst=f"capability:{cap['id']}", kind="versions",
                        evidence_refs=[{"source": "capability_versions",
                                         "version_id": v.version_id}])

    # ── Edges from audit (operator → action) ──
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = audit_log.list_entries(days=lookback_days, limit=5000)
    for r in rows:
        actor_name = (r.get("actor") or {}).get("name")
        if not actor_name:
            continue
        _add_node(g, kind="operator", node_id=actor_name, label=actor_name)
        entity_type = r.get("entity_type", "")
        entity_id = r.get("entity_id", "")
        if entity_type and entity_id:
            _add_edge(g, src=f"operator:{actor_name}",
                        dst=f"{entity_type}:{entity_id}", kind="touched",
                        evidence_refs=[{"source": "audit",
                                         "entry_id": r.get("entry_id")}])

    if persist:
        _persist(g)
    return g


def related(node_id: str, *, lookback_days: int = 30,
              registry: CapabilityRegistry | None = None,
              max_depth: int = 1) -> dict:
    """Return nodes reachable within max_depth hops from node_id."""
    g = build_graph(lookback_days=lookback_days, registry=registry)
    adjacency: dict[str, list] = defaultdict(list)
    for e in g.edges:
        adjacency[e.src].append(e)
        adjacency[e.dst].append(e)
    visited = {node_id}
    frontier = [node_id]
    edges_out: list = []
    for _ in range(max_depth):
        next_frontier: list[str] = []
        for cur in frontier:
            for e in adjacency.get(cur, []):
                other = e.dst if e.src == cur else e.src
                if other not in visited:
                    visited.add(other)
                    next_frontier.append(other)
                edges_out.append(e.to_dict() if hasattr(e, "to_dict") else e)
        frontier = next_frontier
    return {
        "anchor": node_id,
        "nodes": [g.nodes[n].to_dict() if hasattr(g.nodes.get(n), "to_dict") else g.nodes.get(n, {"node_id": n})
                    for n in visited],
        "edges": edges_out,
    }


def causal_replay(
    *,
    incident_id: str | None = None,
    correlation_id: str | None = None,
    lookback_days: int = 30,
) -> dict:
    """Reconstruct a timeline that led to an incident, using audit rows and
    incident metadata. Every event has an evidence_ref — no invented causality.
    """
    if not (incident_id or correlation_id):
        raise ValueError("provide incident_id or correlation_id")

    if incident_id:
        inc = incidents.get(incident_id)
        if inc is None:
            return {"error": "incident not found"}
        correlation_id = inc.correlation_id
    rows = audit_log.list_entries(correlation_id=correlation_id,
                                     days=lookback_days, limit=500)
    rows.sort(key=lambda r: r.get("timestamp", ""))
    timeline = [{
        "timestamp": r.get("timestamp"),
        "action": r.get("action"),
        "actor": r.get("actor"),
        "entity_type": r.get("entity_type"),
        "entity_id": r.get("entity_id"),
        "evidence_ref": {"source": "audit_log", "entry_id": r.get("entry_id")},
    } for r in rows]

    # Root-cause candidates: actions immediately preceding the incident
    # whose impact intersects the incident's impacted_capabilities
    candidates = []
    if incident_id:
        inc = incidents.get(incident_id)
        impacted = set(inc.impacted_capabilities or [])
        for r in rows[:5]:
            if r.get("entity_type") == "capability" and r.get("entity_id") in impacted:
                candidates.append({
                    "action": r.get("action"),
                    "actor": (r.get("actor") or {}).get("name"),
                    "entity_id": r.get("entity_id"),
                    "evidence_ref": {"source": "audit_log",
                                       "entry_id": r.get("entry_id")},
                })
    return {
        "incident_id": incident_id,
        "correlation_id": correlation_id,
        "timeline": timeline,
        "root_cause_candidates": candidates,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Internal ───────────────────────────────────────────────────────────


def _add_node(g: KnowledgeGraph, *, kind: str, node_id: str, label: str,
                attributes: dict | None = None) -> None:
    key = f"{kind}:{node_id}"
    if key in g.nodes:
        return
    g.nodes[key] = Node(node_id=key, kind=kind, label=label,
                          attributes=attributes or {})


def _add_edge(g: KnowledgeGraph, *, src: str, dst: str, kind: str,
                evidence_refs: list | None = None, weight: float = 1.0) -> None:
    g.edges.append(Edge(src=src, dst=dst, kind=kind,
                          evidence_refs=evidence_refs or [], weight=weight))


def _persist(g: KnowledgeGraph) -> None:
    _GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (_GRAPH_DIR / f"{stamp}.json").write_text(
        json.dumps(g.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
