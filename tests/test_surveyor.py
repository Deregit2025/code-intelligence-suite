"""Unit tests for the Surveyor agent."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.agents.surveyor import Surveyor
from src.graph.knowledge_graph import KnowledgeGraph


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Create a minimal fake Python repo for testing."""
    (tmp_path / "src").mkdir()

    (tmp_path / "src" / "main.py").write_text(
        '''"""Main entry point."""
import os
from src.utils import helper

def main():
    helper.run()
'''
    )
    (tmp_path / "src" / "utils.py").write_text(
        '''"""Utility functions."""
import json

def helper_run():
    pass

def _internal():
    pass
'''
    )
    (tmp_path / "src" / "dead_module.py").write_text(
        '''"""Unused module."""
def orphan():
    pass
'''
    )
    return tmp_path


def test_surveyor_runs_without_error(sample_repo: Path) -> None:
    kg = KnowledgeGraph()
    surveyor = Surveyor(sample_repo, kg)
    surveyor.run()

    nodes = kg.module_graph.nodes_data()
    assert len(nodes) > 0


def test_surveyor_detects_modules(sample_repo: Path) -> None:
    kg = KnowledgeGraph()
    surveyor = Surveyor(sample_repo, kg)
    surveyor.run()

    paths = set(kg.module_graph.nodes_data().keys())
    assert any("main" in p for p in paths)
    assert any("utils" in p for p in paths)


def test_surveyor_dead_code_candidate(sample_repo: Path) -> None:
    kg = KnowledgeGraph()
    surveyor = Surveyor(sample_repo, kg)
    surveyor.run()

    nodes = kg.module_graph.nodes_data()
    dead = [p for p, d in nodes.items() if d.get("is_dead_code_candidate")]
    # dead_module.py imports nothing and is imported by nothing
    assert any("dead" in p for p in dead)


def test_surveyor_pagerank_assigned(sample_repo: Path) -> None:
    kg = KnowledgeGraph()
    surveyor = Surveyor(sample_repo, kg)
    surveyor.run()

    nodes = kg.module_graph.nodes_data()
    scores = [d.get("pagerank_score", 0.0) for d in nodes.values()]
    assert any(s > 0 for s in scores)