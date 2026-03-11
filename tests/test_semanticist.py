"""Unit tests for the Semanticist agent (LLM-free paths)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.semanticist import Semanticist
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.nodes import ModuleNode


def test_semanticist_skipped_in_static_mode(tmp_path: Path) -> None:
    from src.config import CONFIG

    CONFIG.static_only = True
    kg = KnowledgeGraph()
    kg.add_module(ModuleNode(path="test.py"))

    s = Semanticist(tmp_path, kg)
    s.run()  # Should not raise or call any LLM

    assert s.purpose_statements == {}
    CONFIG.static_only = False  # reset


def test_parse_purpose_response() -> None:
    from src.agents.semanticist import Semanticist
    from src.graph.knowledge_graph import KnowledgeGraph
    from pathlib import Path

    s = Semanticist(Path("."), KnowledgeGraph())

    response = """PURPOSE: This module handles the ingestion of raw CSV files from external sources into the staging layer.
DRIFT: none"""
    purpose, drift = s._parse_purpose_response(response)
    assert "ingestion" in purpose
    assert drift is None


def test_parse_purpose_response_with_drift() -> None:
    from src.agents.semanticist import Semanticist
    from src.graph.knowledge_graph import KnowledgeGraph
    from pathlib import Path

    s = Semanticist(Path("."), KnowledgeGraph())

    response = """PURPOSE: Computes weekly revenue rollups from raw order data.
DRIFT: DOCUMENTATION_DRIFT: Docstring says 'daily' but implementation uses 7-day windows."""
    purpose, drift = s._parse_purpose_response(response)
    assert "revenue" in purpose
    assert drift is not None
    assert "DOCUMENTATION_DRIFT" in drift


def test_extract_module_docstring() -> None:
    from src.agents.semanticist import Semanticist
    from src.graph.knowledge_graph import KnowledgeGraph
    from pathlib import Path

    s = Semanticist(Path("."), KnowledgeGraph())

    source = '"""This module handles user authentication."""\nimport os\n'
    docstring = s._extract_module_docstring(source)
    assert docstring == "This module handles user authentication."