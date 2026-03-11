"""
Central knowledge graph built on NetworkX.

Wraps two NetworkX DiGraphs:
  1. module_graph  – module-level import/dependency structure
  2. lineage_graph – data flow (dataset nodes + transformation nodes)

Provides structural analysis: PageRank, SCC detection, BFS blast-radius, etc.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import networkx as nx

from src.models.edges import BaseEdge, EdgeType, ImportsEdge
from src.models.nodes import DatasetNode, ModuleNode, TransformationNode
from src.utils.logging_utils import get_logger

logger = get_logger(__name__)


class ModuleGraph:
    """Import-dependency graph of source modules."""

    def __init__(self) -> None:
        self.G: nx.DiGraph = nx.DiGraph()

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_module(self, node: ModuleNode) -> None:
        self.G.add_node(node.path, **node.model_dump())

    def add_import_edge(self, source_path: str, target_path: str, weight: int = 1) -> None:
        if self.G.has_edge(source_path, target_path):
            self.G[source_path][target_path]["weight"] += weight
        else:
            self.G.add_edge(source_path, target_path, weight=weight, edge_type=EdgeType.IMPORTS)

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def compute_pagerank(self) -> dict[str, float]:
        if len(self.G) == 0:
            return {}
        try:
            return nx.pagerank(self.G, weight="weight")
        except nx.PowerIterationFailedConvergence:
            return nx.pagerank(self.G, max_iter=200, tol=1e-4)

    def find_circular_dependencies(self) -> list[list[str]]:
        """Return all strongly connected components of size > 1."""
        return [
            list(scc)
            for scc in nx.strongly_connected_components(self.G)
            if len(scc) > 1
        ]

    def top_modules_by_pagerank(self, n: int = 10) -> list[tuple[str, float]]:
        pr = self.compute_pagerank()
        return sorted(pr.items(), key=lambda x: x[1], reverse=True)[:n]

    def get_importers(self, module_path: str) -> list[str]:
        """Who imports *module_path*?"""
        return list(self.G.predecessors(module_path))

    def get_imports(self, module_path: str) -> list[str]:
        """What does *module_path* import?"""
        return list(self.G.successors(module_path))

    def blast_radius_modules(self, module_path: str) -> list[str]:
        """BFS: all modules that (transitively) import *module_path*."""
        if module_path not in self.G:
            return []
        # Reverse graph: edges point from target to source
        rev = self.G.reverse(copy=False)
        return list(nx.bfs_tree(rev, module_path).nodes()) - {module_path}  # type: ignore

    def nodes_data(self) -> dict[str, Any]:
        return dict(self.G.nodes(data=True))

    def edges_data(self) -> list[dict]:
        return [
            {"source": u, "target": v, **d}
            for u, v, d in self.G.edges(data=True)
        ]


class DataLineageGraph:
    """
    Data flow DAG: dataset nodes connected through transformation nodes.

    Node types stored in this graph:
      - "dataset"        → DatasetNode
      - "transformation" → TransformationNode
    """

    def __init__(self) -> None:
        self.G: nx.DiGraph = nx.DiGraph()

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_dataset(self, node: DatasetNode) -> None:
        self.G.add_node(node.name, node_type="dataset", **node.model_dump())

    def add_transformation(self, node: TransformationNode) -> None:
        self.G.add_node(node.node_id, node_type="transformation", **node.model_dump())

    def add_lineage_edge(
        self,
        source: str,
        target: str,
        edge_type: EdgeType,
        source_file: str = "",
        line_range: tuple[int, int] = (0, 0),
    ) -> None:
        self.G.add_edge(
            source,
            target,
            edge_type=edge_type.value,
            source_file=source_file,
            line_range=line_range,
        )

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def find_sources(self) -> list[str]:
        """Nodes with in-degree 0 (no upstream producers)."""
        return [n for n in self.G.nodes if self.G.in_degree(n) == 0]

    def find_sinks(self) -> list[str]:
        """Nodes with out-degree 0 (nothing consumes them)."""
        return [n for n in self.G.nodes if self.G.out_degree(n) == 0]

    def blast_radius(self, node_name: str) -> list[str]:
        """
        All downstream nodes that would be affected if *node_name* changes.
        Returns list of node names (excluding the node itself).
        """
        if node_name not in self.G:
            return []
        descendants = nx.descendants(self.G, node_name)
        return list(descendants)

    def upstream_lineage(self, node_name: str) -> list[str]:
        """All upstream ancestors of *node_name*."""
        if node_name not in self.G:
            return []
        return list(nx.ancestors(self.G, node_name))

    def shortest_path(self, source: str, target: str) -> Optional[list[str]]:
        try:
            return nx.shortest_path(self.G, source, target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def get_dataset_nodes(self) -> dict[str, dict]:
        return {n: d for n, d in self.G.nodes(data=True) if d.get("node_type") == "dataset"}

    def get_transformation_nodes(self) -> dict[str, dict]:
        return {n: d for n, d in self.G.nodes(data=True) if d.get("node_type") == "transformation"}

    def nodes_data(self) -> dict[str, Any]:
        return dict(self.G.nodes(data=True))

    def edges_data(self) -> list[dict]:
        return [
            {"source": u, "target": v, **d}
            for u, v, d in self.G.edges(data=True)
        ]


class KnowledgeGraph:
    """
    Top-level container that owns both the module graph and the lineage graph.
    This is the object passed between agents and serialised to disk.
    """

    def __init__(self) -> None:
        self.module_graph = ModuleGraph()
        self.lineage_graph = DataLineageGraph()

    # Convenience pass-throughs
    def add_module(self, node: ModuleNode) -> None:
        self.module_graph.add_module(node)

    def add_import_edge(self, source: str, target: str, weight: int = 1) -> None:
        self.module_graph.add_import_edge(source, target, weight)

    def add_dataset(self, node: DatasetNode) -> None:
        self.lineage_graph.add_dataset(node)

    def add_transformation(self, node: TransformationNode) -> None:
        self.lineage_graph.add_transformation(node)

    def add_lineage_edge(self, source: str, target: str, edge_type: EdgeType, **kwargs) -> None:
        self.lineage_graph.add_lineage_edge(source, target, edge_type, **kwargs)

    def summary(self) -> dict[str, int]:
        return {
            "modules": self.module_graph.G.number_of_nodes(),
            "module_edges": self.module_graph.G.number_of_edges(),
            "datasets": len(self.lineage_graph.get_dataset_nodes()),
            "transformations": len(self.lineage_graph.get_transformation_nodes()),
            "lineage_edges": self.lineage_graph.G.number_of_edges(),
            "circular_dep_groups": len(self.module_graph.find_circular_dependencies()),
        }