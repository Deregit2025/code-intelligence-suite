"""
Pydantic schemas for all knowledge-graph node types.

These are the canonical data models that flow between every agent
in the Cartographer pipeline.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class StorageType(str, Enum):
    TABLE = "table"
    FILE = "file"
    STREAM = "stream"
    API = "api"
    UNKNOWN = "unknown"


class Language(str, Enum):
    PYTHON = "python"
    SQL = "sql"
    YAML = "yaml"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    NOTEBOOK = "notebook"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Node schemas
# ---------------------------------------------------------------------------


class ModuleNode(BaseModel):
    """Represents a single source file / module in the codebase."""

    # Identity
    path: str = Field(..., description="Repo-relative path, e.g. src/transforms/revenue.py")
    language: Language = Language.PYTHON

    # Semantic (filled by Semanticist)
    purpose_statement: Optional[str] = None
    domain_cluster: Optional[str] = None
    docstring_drift: bool = False  # True when LLM purpose ≠ existing docstring

    # Structural (filled by Surveyor)
    imports: list[str] = Field(default_factory=list)
    exported_symbols: list[str] = Field(default_factory=list)
    lines_of_code: int = 0
    cyclomatic_complexity: float = 0.0
    comment_ratio: float = 0.0

    # Change velocity (filled by Surveyor via git)
    change_velocity_30d: int = 0  # number of commits touching this file in last N days
    last_modified: Optional[datetime] = None

    # Graph metrics (filled after graph construction)
    pagerank_score: float = 0.0
    in_degree: int = 0
    out_degree: int = 0

    # Dead-code heuristic
    is_dead_code_candidate: bool = False

    class Config:
        use_enum_values = True


class DatasetNode(BaseModel):
    """Represents a data table, file, stream, or API endpoint."""

    name: str = Field(..., description="Logical name: table name, file path, topic name, etc.")
    storage_type: StorageType = StorageType.UNKNOWN

    # Schema snapshot (best-effort, may be None for dynamic schemas)
    schema_snapshot: Optional[dict[str, str]] = None  # {column_name: data_type}

    # Ownership / SLA metadata (often parsed from YAML config)
    freshness_sla: Optional[str] = None  # e.g. "daily", "hourly"
    owner: Optional[str] = None

    # Whether this is a source-of-truth table (no upstream in the lineage graph)
    is_source_of_truth: bool = False

    # Graph metrics
    in_degree: int = 0
    out_degree: int = 0

    class Config:
        use_enum_values = True


class FunctionNode(BaseModel):
    """Represents a single function or method."""

    qualified_name: str = Field(
        ..., description="Fully qualified name, e.g. src.transforms.revenue.compute_mrr"
    )
    parent_module: str = Field(..., description="Repo-relative path of the containing module")
    signature: str = ""  # raw signature string
    purpose_statement: Optional[str] = None

    # Usage metrics (filled by Surveyor)
    call_count_within_repo: int = 0
    is_public_api: bool = True  # False if name starts with _

    # Location
    start_line: int = 0
    end_line: int = 0


class TransformationNode(BaseModel):
    """Represents a data transformation step connecting datasets."""

    # Identity – auto-generated key
    node_id: str = Field(..., description="Unique id, e.g. hash of source_file + line_range")

    source_datasets: list[str] = Field(default_factory=list)
    target_datasets: list[str] = Field(default_factory=list)

    transformation_type: str = "unknown"  # e.g. pandas, spark, dbt_model, sql, airflow_task

    # Source location
    source_file: str = ""
    line_range: tuple[int, int] = (0, 0)

    # Optional raw SQL for SQL-based transformations
    sql_query: Optional[str] = None