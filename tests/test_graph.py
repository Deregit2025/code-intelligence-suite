"""Unit tests for KnowledgeGraph, ModuleGraph, and DataLineageGraph."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.graph.graph_serializers import (
    load_lineage_graph,
    load_module_graph,
    save_lineage_graph,
    save_module_graph,
)
from src.graph.knowledge_graph import DataLineageGraph, KnowledgeGraph, ModuleGraph
from src.models.edges import EdgeType
from src.models.nodes import DatasetNode, ModuleNode, StorageType, TransformationNode


# ---------------------------------------------------------------------------
# ModuleGraph
# ---------------------------------------------------------------------------


def test_module_graph_add_and_pagerank() -> None:
    mg = ModuleGraph()
    mg.add_module(ModuleNode(path="a.py"))
    mg.add_module(ModuleNode(path="b.py"))
    mg.add_module(ModuleNode(path="c.py"))
    mg.add_import_edge("a.py", "b.py")
    mg.add_import_edge("c.py", "b.py")

    pr = mg.compute_pagerank()
    assert "b.py" in pr
    # b.py is imported by 2 modules → should have higher PageRank
    assert pr["b.py"] >= pr.get("a.py", 0)


def test_circular_dependency_detection() -> None:
    mg = ModuleGraph()
    for p in ["x.py", "y.py", "z.py"]:
        mg.add_module(ModuleNode(path=p))
    mg.add_import_edge("x.py", "y.py")
    mg.add_import_edge("y.py", "z.py")
    mg.add_import_edge("z.py", "x.py")  # creates cycle

    sccs = mg.find_circular_dependencies()
    assert len(sccs) == 1
    assert set(sccs[0]) == {"x.py", "y.py", "z.py"}


def test_blast_radius_modules() -> None:
    mg = ModuleGraph()
    for p in ["core.py", "service.py", "api.py", "test.py"]:
        mg.add_module(ModuleNode(path=p))
    mg.add_import_edge("service.py", "core.py")
    mg.add_import_edge("api.py", "service.py")
    mg.add_import_edge("test.py", "core.py")

    # Who imports core.py (transitively)?
    affected = mg.blast_radius_modules("core.py")
    assert "service.py" in affected
    assert "test.py" in affected


# ---------------------------------------------------------------------------
# DataLineageGraph
# ---------------------------------------------------------------------------


def test_lineage_sources_and_sinks() -> None:
    lg = DataLineageGraph()
    lg.add_dataset(DatasetNode(name="raw_events", storage_type=StorageType.TABLE))
    lg.add_dataset(DatasetNode(name="daily_summary", storage_type=StorageType.TABLE))
    t = TransformationNode(
        node_id="t1",
        source_datasets=["raw_events"],
        target_datasets=["daily_summary"],
        transformation_type="sql",
        source_file="models/summary.sql",
    )
    lg.add_transformation(t)
    lg.add_lineage_edge("raw_events", "t1", EdgeType.PRODUCES)
    lg.add_lineage_edge("t1", "daily_summary", EdgeType.CONSUMES)

    sources = lg.find_sources()
    sinks = lg.find_sinks()

    assert "raw_events" in sources
    assert "daily_summary" in sinks


def test_blast_radius_lineage() -> None:
    lg = DataLineageGraph()
    for name in ["A", "B", "C", "D"]:
        lg.add_dataset(DatasetNode(name=name, storage_type=StorageType.TABLE))

    lg.add_lineage_edge("A", "B", EdgeType.PRODUCES)
    lg.add_lineage_edge("B", "C", EdgeType.PRODUCES)
    lg.add_lineage_edge("B", "D", EdgeType.PRODUCES)

    affected = lg.blast_radius("A")
    assert "B" in affected
    assert "C" in affected
    assert "D" in affected
    assert "A" not in affected


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------


def test_module_graph_serialization_roundtrip(tmp_path: Path) -> None:
    mg = ModuleGraph()
    mg.add_module(ModuleNode(path="alpha.py", lines_of_code=100))
    mg.add_module(ModuleNode(path="beta.py", lines_of_code=50))
    mg.add_import_edge("alpha.py", "beta.py")

    save_path = tmp_path / "module_graph.json"
    save_module_graph(mg, save_path)

    loaded = load_module_graph(save_path)
    assert loaded.G.number_of_nodes() == 2
    assert loaded.G.number_of_edges() == 1


def test_lineage_graph_serialization_roundtrip(tmp_path: Path) -> None:
    lg = DataLineageGraph()
    lg.add_dataset(DatasetNode(name="src_table", storage_type=StorageType.TABLE))
    lg.add_dataset(DatasetNode(name="out_table", storage_type=StorageType.TABLE))
    t = TransformationNode(
        node_id="txfm1",
        transformation_type="sql",
        source_file="query.sql",
    )
    lg.add_transformation(t)
    lg.add_lineage_edge("src_table", "txfm1", EdgeType.PRODUCES)
    lg.add_lineage_edge("txfm1", "out_table", EdgeType.CONSUMES)

    save_path = tmp_path / "lineage_graph.json"
    save_lineage_graph(lg, save_path)

    loaded = load_lineage_graph(save_path)
    assert loaded.G.number_of_nodes() == 3
    assert loaded.G.number_of_edges() == 2