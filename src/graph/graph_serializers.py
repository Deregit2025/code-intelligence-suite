"""
Serialisation helpers for knowledge graph → JSON (disk) and back.
Uses NetworkX's built-in node_link_data format for portability.
"""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
from networkx.readwrite import json_graph

from src.graph.knowledge_graph import DataLineageGraph, KnowledgeGraph, ModuleGraph
from src.utils.logging_utils import get_logger

logger = get_logger(__name__)


def _serialize_graph(G: nx.DiGraph) -> dict:
    """Convert a NetworkX DiGraph to a JSON-serialisable dict."""
    data = json_graph.node_link_data(G)
    # Convert any non-serialisable types (tuples, etc.)
    return json.loads(json.dumps(data, default=str))


def _deserialize_graph(data: dict) -> nx.DiGraph:
    return json_graph.node_link_graph(data, directed=True, multigraph=False)


def save_module_graph(graph: ModuleGraph, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = _serialize_graph(graph.G)
    path.write_text(json.dumps(serialized, indent=2), encoding="utf-8")
    logger.info(f"Module graph saved → {path}")


def save_lineage_graph(graph: DataLineageGraph, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = _serialize_graph(graph.G)
    path.write_text(json.dumps(serialized, indent=2), encoding="utf-8")
    logger.info(f"Lineage graph saved → {path}")


def load_module_graph(path: Path) -> ModuleGraph:
    data = json.loads(path.read_text(encoding="utf-8"))
    mg = ModuleGraph()
    mg.G = _deserialize_graph(data)
    return mg


def load_lineage_graph(path: Path) -> DataLineageGraph:
    data = json.loads(path.read_text(encoding="utf-8"))
    lg = DataLineageGraph()
    lg.G = _deserialize_graph(data)
    return lg


def save_knowledge_graph(kg: KnowledgeGraph, cartography_dir: Path) -> None:
    save_module_graph(kg.module_graph, cartography_dir / "module_graph.json")
    save_lineage_graph(kg.lineage_graph, cartography_dir / "lineage_graph.json")


def load_knowledge_graph(cartography_dir: Path) -> KnowledgeGraph:
    kg = KnowledgeGraph()
    mg_path = cartography_dir / "module_graph.json"
    lg_path = cartography_dir / "lineage_graph.json"
    if mg_path.exists():
        kg.module_graph = load_module_graph(mg_path)
    if lg_path.exists():
        kg.lineage_graph = load_lineage_graph(lg_path)
    return kg