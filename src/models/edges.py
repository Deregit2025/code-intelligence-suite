"""
Pydantic schemas for knowledge-graph edge types.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class EdgeType(str, Enum):
    IMPORTS = "IMPORTS"
    PRODUCES = "PRODUCES"
    CONSUMES = "CONSUMES"
    CALLS = "CALLS"
    CONFIGURES = "CONFIGURES"


class BaseEdge(BaseModel):
    source: str = Field(..., description="Source node identifier (path or qualified name)")
    target: str = Field(..., description="Target node identifier")
    edge_type: EdgeType
    weight: float = 1.0
    metadata: dict = Field(default_factory=dict)

    class Config:
        use_enum_values = True


class ImportsEdge(BaseEdge):
    """source_module → target_module via Python import or relative path."""

    edge_type: EdgeType = EdgeType.IMPORTS
    import_count: int = 1  # how many symbols are imported


class ProducesEdge(BaseEdge):
    """transformation_node → dataset_node (data lineage: write side)."""

    edge_type: EdgeType = EdgeType.PRODUCES
    source_file: str = ""
    line_range: tuple[int, int] = (0, 0)


class ConsumesEdge(BaseEdge):
    """transformation_node → dataset_node (data lineage: read side)."""

    edge_type: EdgeType = EdgeType.CONSUMES
    source_file: str = ""
    line_range: tuple[int, int] = (0, 0)


class CallsEdge(BaseEdge):
    """function_node → function_node (call graph)."""

    edge_type: EdgeType = EdgeType.CALLS
    call_site_line: Optional[int] = None


class ConfiguresEdge(BaseEdge):
    """config_file → module/pipeline (YAML/ENV relationship)."""

    edge_type: EdgeType = EdgeType.CONFIGURES
    config_key: Optional[str] = None