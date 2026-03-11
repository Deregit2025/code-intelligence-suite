"""Unit tests for the Navigator agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.navigator import NavigatorTools
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.edges import EdgeType
from src.models.nodes import DatasetNode, ModuleNode, StorageType, TransformationNode


@pytest.fixture
def kg_with_data() -> KnowledgeGraph:
    kg = KnowledgeGraph()

    # Modules
    kg.add_module(ModuleNode(
        path="src/ingestion/loader.py",
        purpose_statement="Loads raw CSV files from S3 into the data warehouse.",
    ))
    kg.add_module(ModuleNode(
        path="src/transforms/revenue.py",
        purpose_statement="Computes monthly revenue metrics from order data.",
    ))
    kg.module_graph.G.nodes["src/transforms/revenue.py"]["purpose_statement"] = (
        "Computes monthly revenue metrics from order data."
    )
    kg.module_graph.add_import_edge("src/transforms/revenue.py", "src/ingestion/loader.py")

    # Datasets
    kg.add_dataset(DatasetNode(name="raw_orders", storage_type=StorageType.TABLE))
    kg.add_dataset(DatasetNode(name="revenue_monthly", storage_type=StorageType.TABLE))

    t = TransformationNode(
        node_id="t_revenue",
        source_datasets=["raw_orders"],
        target_datasets=["revenue_monthly"],
        transformation_type="pandas",
        source_file="src/transforms/revenue.py",
    )
    kg.add_transformation(t)
    kg.add_lineage_edge("raw_orders", "t_revenue", EdgeType.PRODUCES)
    kg.add_lineage_edge("t_revenue", "revenue_monthly", EdgeType.CONSUMES)

    return kg


def test_find_implementation_keyword(kg_with_data: KnowledgeGraph) -> None:
    tools = NavigatorTools(kg_with_data)
    result = tools.find_implementation("revenue calculation")
    assert "matches" in result
    # Should find revenue.py via keyword fallback
    paths = [m["path"] for m in result["matches"]]
    assert any("revenue" in p for p in paths)


def test_trace_lineage_upstream(kg_with_data: KnowledgeGraph) -> None:
    tools = NavigatorTools(kg_with_data)
    result = tools.trace_lineage("revenue_monthly", direction="upstream")
    assert "upstream" in result
    # raw_orders should appear as upstream (via transformation)
    upstream_names = [n["name"] for n in result["upstream"]]
    assert "raw_orders" in upstream_names or "t_revenue" in upstream_names


def test_blast_radius_module(kg_with_data: KnowledgeGraph) -> None:
    tools = NavigatorTools(kg_with_data)
    result = tools.blast_radius("src/ingestion/loader.py")
    assert "affected_modules" in result
    # revenue.py imports loader.py, so it should be in blast radius
    module_paths = [m["path"] for m in result["affected_modules"]]
    assert any("revenue" in p for p in module_paths)


def test_explain_module_returns_data(kg_with_data: KnowledgeGraph) -> None:
    tools = NavigatorTools(kg_with_data)
    result = tools.explain_module("src/transforms/revenue.py")
    assert "explanation" in result
    assert "revenue" in result["explanation"].lower()
    assert result["evidence_source"] in ("llm_inference", "static_analysis", "error")