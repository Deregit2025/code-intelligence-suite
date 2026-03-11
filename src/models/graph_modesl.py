"""
Top-level graph container models used as serialisation targets.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.models.edges import BaseEdge
from src.models.nodes import DatasetNode, FunctionNode, ModuleNode, TransformationNode


class ModuleGraph(BaseModel):
    """The structural module-import graph produced by the Surveyor."""

    nodes: dict[str, ModuleNode] = Field(default_factory=dict)  # keyed by path
    edges: list[BaseEdge] = Field(default_factory=list)

    # Graph-level metrics
    circular_dependency_groups: list[list[str]] = Field(default_factory=list)
    top_pagerank_modules: list[str] = Field(default_factory=list)  # top-10 by PageRank
    high_velocity_files: list[str] = Field(default_factory=list)  # top-20% by git commits


class DataLineageGraph(BaseModel):
    """The data flow DAG produced by the Hydrologist."""

    dataset_nodes: dict[str, DatasetNode] = Field(default_factory=dict)  # keyed by name
    transformation_nodes: dict[str, TransformationNode] = Field(default_factory=dict)
    edges: list[BaseEdge] = Field(default_factory=list)

    # Convenience sets
    source_datasets: list[str] = Field(default_factory=list)   # in-degree 0
    sink_datasets: list[str] = Field(default_factory=list)     # out-degree 0


class SemanticIndex(BaseModel):
    """Semantic analysis results produced by the Semanticist."""

    module_purposes: dict[str, str] = Field(default_factory=dict)  # path → purpose statement
    domain_clusters: dict[str, str] = Field(default_factory=dict)  # path → domain label
    doc_drift_flags: list[str] = Field(default_factory=list)        # paths with drift
    day_one_answers: dict[str, str] = Field(default_factory=dict)   # question_id → answer


class KnowledgeGraphSnapshot(BaseModel):
    """Full serialisable snapshot of the knowledge graph."""

    repo_root: str = ""
    analysed_at: str = ""
    module_graph: ModuleGraph = Field(default_factory=ModuleGraph)
    lineage_graph: DataLineageGraph = Field(default_factory=DataLineageGraph)
    semantic_index: SemanticIndex = Field(default_factory=SemanticIndex)
    function_nodes: dict[str, FunctionNode] = Field(default_factory=dict)

    # Raw artifact paths (populated after Archivist runs)
    artifacts: dict[str, str] = Field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "modules": len(self.module_graph.nodes),
            "datasets": len(self.lineage_graph.dataset_nodes),
            "transformations": len(self.lineage_graph.transformation_nodes),
            "functions": len(self.function_nodes),
            "circular_deps": len(self.module_graph.circular_dependency_groups),
            "doc_drift_flags": len(self.semantic_index.doc_drift_flags),
        }