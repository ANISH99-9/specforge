"""
SpecForge — Dependency Graph
Graph-based localization: given a broken field, find exactly which
pipeline nodes consume it, so repair can be surgically targeted.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set


class NodeType(str, Enum):
    TABLE    = "table"
    COLUMN   = "column"
    ENDPOINT = "endpoint"
    COMPONENT = "component"
    ROLE     = "role"
    RULE     = "rule"
    PAGE     = "page"


@dataclass
class GraphNode:
    id: str           # e.g. "db.users.email", "api.GET_users", "ui.UserTable"
    node_type: NodeType
    stage: str        # which pipeline stage produced this node
    data: dict = field(default_factory=dict)


class DependencyGraph:
    """
    Directed graph where an edge  A → B means
    'node A depends on node B' (A is a consumer of B).

    Usage:
        graph.add_edge("api.GET_users", "db.users")
        graph.get_consumers("db.users")  # → ["api.GET_users", ...]
        graph.get_broken_refs()          # → list of {consumer, missing_dep}
    """

    def __init__(self) -> None:
        self.nodes: Dict[str, GraphNode] = {}
        # forward: consumer → list of dependencies
        self._edges: Dict[str, List[str]] = {}
        # reverse: dependency → list of consumers
        self._reverse: Dict[str, List[str]] = {}

    # ── Graph construction ──────────────────────────────────────────

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.id] = node
        if node.id not in self._edges:
            self._edges[node.id] = []

    def add_edge(self, consumer_id: str, dependency_id: str) -> None:
        """Record that consumer_id depends on dependency_id."""
        self._edges.setdefault(consumer_id, [])
        if dependency_id not in self._edges[consumer_id]:
            self._edges[consumer_id].append(dependency_id)

        self._reverse.setdefault(dependency_id, [])
        if consumer_id not in self._reverse[dependency_id]:
            self._reverse[dependency_id].append(consumer_id)

    # ── Query ───────────────────────────────────────────────────────

    def get_consumers(self, node_id: str) -> List[str]:
        """All nodes that depend on node_id."""
        return list(self._reverse.get(node_id, []))

    def get_dependencies(self, node_id: str) -> List[str]:
        """All nodes that node_id depends on."""
        return list(self._edges.get(node_id, []))

    def get_broken_refs(self) -> List[dict]:
        """
        Returns a list of broken dependency references:
        every edge where the dependency node does not exist in the graph.
        """
        broken = []
        for consumer_id, deps in self._edges.items():
            for dep_id in deps:
                if dep_id not in self.nodes:
                    consumer = self.nodes.get(consumer_id)
                    broken.append({
                        "consumer":       consumer_id,
                        "consumer_stage": consumer.stage if consumer else "unknown",
                        "missing_dep":    dep_id,
                        "impact":         self._cascade_impact(consumer_id),
                    })
        return broken

    def _cascade_impact(self, start_id: str) -> List[str]:
        """BFS: all nodes transitively consuming start_id."""
        visited: Set[str] = set()
        queue = [start_id]
        while queue:
            current = queue.pop(0)
            for consumer in self._reverse.get(current, []):
                if consumer not in visited:
                    visited.add(consumer)
                    queue.append(consumer)
        return list(visited)

    # ── Build from AppConfig ────────────────────────────────────────

    def build_from_app_config(self, app_config) -> None:  # type: ignore[annotation]
        """
        Populate the graph from a complete AppConfig.
        Call this after Stage 4 (refinement) completes.
        """
        from validation.schema_defs import AppConfig  # local import to avoid circular

        # ── DB nodes ──
        for table in app_config.db_schema.tables:
            tbl_id = f"db.{table.name}"
            self.add_node(GraphNode(id=tbl_id, node_type=NodeType.TABLE, stage="schema",
                                   data={"name": table.name}))
            for col in table.columns:
                col_id = f"db.{table.name}.{col.name}"
                self.add_node(GraphNode(id=col_id, node_type=NodeType.COLUMN, stage="schema",
                                       data=col.model_dump()))
                self.add_edge(col_id, tbl_id)  # column depends on its table
                if col.foreign_key:
                    parts = col.foreign_key.split(".")
                    if len(parts) == 2:
                        ref_id = f"db.{parts[0]}.{parts[1]}"
                        self.add_edge(col_id, ref_id)  # FK dependency

        # ── API nodes ──
        for ep in app_config.api_schema.endpoints:
            ep_id = f"api.{ep.id}"
            self.add_node(GraphNode(id=ep_id, node_type=NodeType.ENDPOINT, stage="schema",
                                   data={"method": ep.method, "path": ep.path}))
            for tbl_name in ep.db_tables:
                self.add_edge(ep_id, f"db.{tbl_name}")

        # ── Auth role nodes ──
        for role in app_config.auth_schema.roles:
            role_id = f"role.{role}"
            self.add_node(GraphNode(id=role_id, node_type=NodeType.ROLE, stage="schema",
                                   data={"name": role}))

        # ── UI component nodes ──
        def _add_component(comp, parent_id: Optional[str] = None) -> None:
            comp_id = f"ui.{comp.id}"
            self.add_node(GraphNode(id=comp_id, node_type=NodeType.COMPONENT, stage="schema",
                                   data={"type": comp.type, "label": comp.label}))
            if parent_id:
                self.add_edge(comp_id, parent_id)  # child depends on parent existing
            if comp.data_binding:
                ep_ref = f"api.{comp.data_binding.endpoint_id}"
                self.add_edge(comp_id, ep_ref)  # UI binds to API endpoint
            if comp.actions:
                for action_ep_id in comp.actions:
                    self.add_edge(comp_id, f"api.{action_ep_id}")
            for child in (comp.children or []):
                _add_component(child, comp_id)

        for page in app_config.ui_schema.pages:
            _add_component(page)

    # ── Serialization ───────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "nodes":   {nid: {"type": n.node_type, "stage": n.stage} for nid, n in self.nodes.items()},
            "edges":   self._edges,
            "reverse": self._reverse,
        }
