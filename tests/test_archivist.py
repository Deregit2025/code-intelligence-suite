"""Unit tests for the Archivist agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.archivist import Archivist
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.nodes import DatasetNode, ModuleNode, StorageType


@pytest.fixture
def populated_kg() -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_module(ModuleNode(path="src/ingest.py", lines_of_code=200, pagerank_score=0.4))
    kg.add_module(ModuleNode(path="src/transform.py", lines_of_code=150, pagerank_score=0.2))
    kg.add_dataset(DatasetNode(name="raw_events", storage_type=StorageType.TABLE))
    kg.add_dataset(DatasetNode(name="daily_report", storage_type=StorageType.TABLE))
    kg.module_graph.G.graph["high_velocity_files"] = ["src/ingest.py"]
    kg.module_graph.G.graph["day_one_answers"] = {
        "Q1": "Data enters via src/ingest.py reading from raw_events.",
    }
    return kg


def test_archivist_generates_codebase_md(tmp_path: Path, populated_kg: KnowledgeGraph) -> None:
    archivist = Archivist(
        repo_root=tmp_path,
        kg=populated_kg,
        cartography_dir=tmp_path / ".cartography",
        purpose_statements={"src/ingest.py": "Ingests raw event data from the source system."},
    )
    artifacts = archivist.run()

    codebase_path = Path(artifacts["codebase_md"])
    assert codebase_path.exists()
    content = codebase_path.read_text()
    assert "Architecture Overview" in content
    assert "Critical Path" in content
    assert "Data Sources" in content


def test_archivist_generates_onboarding_brief(tmp_path: Path, populated_kg: KnowledgeGraph) -> None:
    archivist = Archivist(
        repo_root=tmp_path,
        kg=populated_kg,
        cartography_dir=tmp_path / ".cartography",
        day_one_answers={"Q1": "Data enters via raw_events table."},
    )
    artifacts = archivist.run()

    brief_path = Path(artifacts["onboarding_brief"])
    assert brief_path.exists()
    content = brief_path.read_text()
    assert "Day-One" in content
    assert "Q1" in content


def test_archivist_generates_graph_json(tmp_path: Path, populated_kg: KnowledgeGraph) -> None:
    archivist = Archivist(
        repo_root=tmp_path,
        kg=populated_kg,
        cartography_dir=tmp_path / ".cartography",
    )
    artifacts = archivist.run()

    assert Path(artifacts["module_graph"]).exists()
    assert Path(artifacts["lineage_graph"]).exists()