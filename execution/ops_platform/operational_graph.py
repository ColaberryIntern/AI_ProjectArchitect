"""Operational graph — in-memory graph of capabilities, pipelines, departments,
agents, MCP servers, and the relationships between them.

This is the substrate the recommendation engine, automated discovery, and
duplicate detection all read. It's intentionally a tiny dict-of-dicts
adjacency list — no networkx, no graph DB. The whole graph fits in memory
even at 10K capabilities.

Node types  : capability | department | agent | mcp_server | persona | pipeline
Edge types  :
  followed_by      — runs of B happen shortly after runs of A (temporal)
  co_occurs        — A and B both used in same pipeline (structural)
  depends_on       — A.manifest.dependencies references B
  used_in_dept     — capability ↔ department (from manifest.category +
                     feedback.submitter.department)
  submitted_by     — feedback ↔ capability (per submitter aggregation)
  has_persona      — capability ↔ recommended_user_personas from enrichment

Edges are weighted by how many independent observations support them.
This is the graph's only knob: more observations = stronger edge = more
likely surfaced by recommendation queries.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import feedback_store, pipeline_engine, semantic_analyzer, workflow_runner
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

# Two runs by the same initiator within this window are considered "followed_by"
FOLLOWED_BY_WINDOW = timedelta(minutes=30)

_GRAPH_PERSIST_PATH = OUTPUT_DIR / "ops_platform" / "graph" / "snapshot.json"


@dataclass
class Edge:
    src: str           # "<type>:<id>"
    dst: str
    kind: str
    weight: float = 1.0
    last_observed: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class OperationalGraph:
    nodes: dict[str, dict] = field(default_factory=dict)           # key → {type, id, label, attrs}
    edges: dict[tuple[str, str, str], Edge] = field(default_factory=dict)

    # ── Builders ───────────────────────────────────────────────────────

    def add_node(self, ntype: str, nid: str, label: str = "", **attrs) -> str:
        key = f"{ntype}:{nid}"
        existing = self.nodes.get(key, {})
        existing.setdefault("type", ntype)
        existing.setdefault("id", nid)
        existing["label"] = label or existing.get("label") or nid
        existing.setdefault("attrs", {})
        existing["attrs"].update(attrs or {})
        self.nodes[key] = existing
        return key

    def add_edge(self, src_key: str, dst_key: str, kind: str, weight: float = 1.0) -> None:
        if src_key == dst_key:
            return  # no self-edges
        key = (src_key, dst_key, kind)
        if key in self.edges:
            self.edges[key].weight += weight
            self.edges[key].last_observed = datetime.now(timezone.utc).isoformat()
        else:
            self.edges[key] = Edge(
                src=src_key, dst=dst_key, kind=kind, weight=weight,
                last_observed=datetime.now(timezone.utc).isoformat(),
            )

    # ── Queries ────────────────────────────────────────────────────────

    def neighbors(self, key: str, *, kind: str | None = None,
                  direction: str = "out") -> list[Edge]:
        """Return outgoing (or incoming) edges. Filterable by kind."""
        out: list[Edge] = []
        for edge in self.edges.values():
            if kind and edge.kind != kind:
                continue
            if direction == "out" and edge.src == key:
                out.append(edge)
            elif direction == "in" and edge.dst == key:
                out.append(edge)
            elif direction == "any" and key in (edge.src, edge.dst):
                out.append(edge)
        out.sort(key=lambda e: e.weight, reverse=True)
        return out

    def top_followed_by(self, capability_id: str, top_k: int = 5) -> list[tuple[str, float]]:
        key = f"capability:{capability_id}"
        edges = self.neighbors(key, kind="followed_by", direction="out")
        out: list[tuple[str, float]] = []
        for e in edges[:top_k]:
            _, dst_id = e.dst.split(":", 1)
            out.append((dst_id, e.weight))
        return out

    def top_co_occurs(self, capability_id: str, top_k: int = 5) -> list[tuple[str, float]]:
        key = f"capability:{capability_id}"
        edges = self.neighbors(key, kind="co_occurs", direction="any")
        seen: dict[str, float] = {}
        for e in edges:
            other = e.dst if e.src == key else e.src
            _, oid = other.split(":", 1)
            seen[oid] = seen.get(oid, 0.0) + e.weight
        return sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:top_k]

    def department_capabilities(self, department: str, top_k: int = 20) -> list[tuple[str, float]]:
        key = f"department:{department}"
        edges = self.neighbors(key, kind="used_in_dept", direction="any")
        out: list[tuple[str, float]] = []
        for e in edges[:top_k]:
            other = e.dst if e.src == key else e.src
            ntype, oid = other.split(":", 1)
            if ntype == "capability":
                out.append((oid, e.weight))
        return out

    def persona_capabilities(self, persona: str, top_k: int = 20) -> list[tuple[str, float]]:
        key = f"persona:{persona}"
        edges = self.neighbors(key, kind="has_persona", direction="any")
        out: list[tuple[str, float]] = []
        for e in edges[:top_k]:
            other = e.dst if e.src == key else e.src
            ntype, oid = other.split(":", 1)
            if ntype == "capability":
                out.append((oid, e.weight))
        return out

    def stats(self) -> dict:
        kind_counts: dict[str, int] = defaultdict(int)
        for e in self.edges.values():
            kind_counts[e.kind] += 1
        ntype_counts: dict[str, int] = defaultdict(int)
        for n in self.nodes.values():
            ntype_counts[n["type"]] += 1
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "by_node_type": dict(ntype_counts),
            "by_edge_kind": dict(kind_counts),
        }

    def to_dict(self) -> dict:
        return {
            "nodes": list(self.nodes.values()),
            "edges": [e.to_dict() for e in self.edges.values()],
            "stats": self.stats(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


# ── Public builders ─────────────────────────────────────────────────────


def build_graph(*, registry: CapabilityRegistry | None = None,
                persist: bool = False) -> OperationalGraph:
    """Build the full graph from current data sources. O(N+M+R)."""
    reg = registry or default_registry()
    g = OperationalGraph()

    capabilities = reg.snapshot().capabilities
    cap_ids = {c["id"] for c in capabilities}

    # ── Nodes for capabilities + departments + personas + agents/mcp ───
    for cap in capabilities:
        cap_key = g.add_node(
            "capability", cap["id"], label=cap.get("name", cap["id"]),
            type_of=cap.get("type"),
            category=cap.get("category"),
            usage_count=cap.get("usage_count", 0),
        )
        # department edge
        dept = cap.get("category")
        if dept:
            dept_key = g.add_node("department", dept, label=dept)
            g.add_edge(cap_key, dept_key, "used_in_dept", weight=1.0)

        # depends_on from manifest dependencies
        for dep in cap.get("dependencies") or []:
            if dep in cap_ids:
                dep_key = f"capability:{dep}"
                g.add_edge(cap_key, dep_key, "depends_on", weight=1.0)

        # related agents / mcp servers
        for aid in cap.get("agents_used") or []:
            akey = g.add_node("agent", aid, label=aid)
            g.add_edge(cap_key, akey, "co_occurs", weight=0.5)
        for mid in cap.get("mcp_servers_used") or []:
            mkey = g.add_node("mcp_server", mid, label=mid)
            g.add_edge(cap_key, mkey, "co_occurs", weight=0.5)

        # personas from semantic enrichment
        enrichment = semantic_analyzer.load_enrichment(cap["id"]) or {}
        for persona in enrichment.get("recommended_user_personas") or []:
            pkey = g.add_node("persona", persona, label=persona)
            g.add_edge(cap_key, pkey, "has_persona", weight=1.0)

    # ── followed_by edges from run history (temporal proximity) ────────
    _add_followed_by_edges(g, cap_ids)

    # ── co_occurs edges from pipelines (structural) ────────────────────
    _add_pipeline_edges(g, cap_ids)

    # ── submitted_by edges from feedback (per-submitter) ───────────────
    _add_feedback_edges(g, cap_ids)

    if persist:
        _persist(g)
    return g


def get_persisted() -> dict | None:
    """Return the persisted graph snapshot, or None if never built."""
    if not _GRAPH_PERSIST_PATH.exists():
        return None
    try:
        return json.loads(_GRAPH_PERSIST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── Internal builders ──────────────────────────────────────────────────


def _add_followed_by_edges(g: OperationalGraph, cap_ids: set[str]) -> None:
    """Two runs whose started_at are within FOLLOWED_BY_WINDOW (and have
    the same initiator if known) become a followed_by edge."""
    runs = workflow_runner.list_runs(limit=5000)
    runs = [r for r in runs if r.status in ("succeeded", "retried_succeeded")]
    runs.sort(key=lambda r: r.started_at)

    # Group by initiator (default: anonymous). Use small bucket so the
    # adjacent-runs check is O(N) within the bucket.
    buckets: dict[str, list] = defaultdict(list)
    for r in runs:
        initiator = (r.inputs.get("__initiator") if isinstance(r.inputs, dict) else None) or "anonymous"
        buckets[initiator].append(r)

    for initiator, group in buckets.items():
        for i, run in enumerate(group):
            for nxt in group[i + 1:]:
                try:
                    dt_a = datetime.fromisoformat(run.started_at)
                    dt_b = datetime.fromisoformat(nxt.started_at)
                except ValueError:
                    continue
                if dt_b - dt_a > FOLLOWED_BY_WINDOW:
                    break
                if run.capability_id == nxt.capability_id:
                    continue
                if run.capability_id not in cap_ids or nxt.capability_id not in cap_ids:
                    continue
                g.add_edge(
                    f"capability:{run.capability_id}",
                    f"capability:{nxt.capability_id}",
                    "followed_by",
                    weight=1.0,
                )


def _add_pipeline_edges(g: OperationalGraph, cap_ids: set[str]) -> None:
    for manifest in pipeline_engine.list_pipelines():
        pipeline_id = manifest.get("pipeline_id")
        if not pipeline_id:
            continue
        pkey = g.add_node("pipeline", pipeline_id, label=manifest.get("name", pipeline_id))
        steps = [s for s in (manifest.get("steps") or [])
                 if s.get("capability_id") in cap_ids]
        for s in steps:
            g.add_edge(pkey, f"capability:{s['capability_id']}", "co_occurs", weight=1.0)
        # pairwise co_occurs among steps + sequential followed_by hints
        for i, s in enumerate(steps):
            for t in steps[i + 1:]:
                g.add_edge(
                    f"capability:{s['capability_id']}",
                    f"capability:{t['capability_id']}",
                    "co_occurs",
                    weight=0.5,
                )
            if i + 1 < len(steps):
                nxt = steps[i + 1]
                g.add_edge(
                    f"capability:{s['capability_id']}",
                    f"capability:{nxt['capability_id']}",
                    "followed_by",
                    weight=0.5,
                )


def _add_feedback_edges(g: OperationalGraph, cap_ids: set[str]) -> None:
    """Per-submitter feedback edges feed team_adoption signals + dept linkage."""
    for cap_id in cap_ids:
        records = feedback_store.list_feedback(cap_id)
        for r in records:
            submitter = (r.get("submitter") or {})
            dept = submitter.get("department")
            if dept:
                dept_key = g.add_node("department", dept, label=dept)
                g.add_edge(
                    f"capability:{cap_id}", dept_key, "used_in_dept", weight=0.25
                )


def _persist(g: OperationalGraph) -> None:
    _GRAPH_PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _GRAPH_PERSIST_PATH.write_text(
        json.dumps(g.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
