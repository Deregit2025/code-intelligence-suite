"""
Airflow DAG and dbt schema.yml config parsers.

Extracts pipeline topology from YAML/Python configuration rather than
from runtime code execution – giving structural lineage even when the
Python logic is too dynamic to analyse statically.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from src.utils.logging_utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DAGTask:
    task_id: str
    operator: str
    upstream_task_ids: list[str] = field(default_factory=list)
    downstream_task_ids: list[str] = field(default_factory=list)
    sql: Optional[str] = None
    table: Optional[str] = None
    source_file: str = ""
    line: int = 0


@dataclass
class DAGDefinition:
    dag_id: str
    source_file: str
    tasks: list[DAGTask] = field(default_factory=list)
    schedule_interval: Optional[str] = None
    description: Optional[str] = None


@dataclass
class DBTModel:
    name: str
    source_file: str
    description: Optional[str] = None
    columns: dict[str, str] = field(default_factory=dict)  # col → description
    depends_on: list[str] = field(default_factory=list)    # explicit upstream (from schema.yml)
    tests: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class DBTSource:
    name: str
    database: Optional[str]
    schema: str
    tables: list[str] = field(default_factory=list)
    source_file: str = ""


# ---------------------------------------------------------------------------
# Airflow DAG parser (Python AST-based)
# ---------------------------------------------------------------------------


class AirflowDAGParser:
    """
    Parses Airflow DAG Python files using Python's AST module.
    Extracts DAG IDs, task definitions, and dependency edges set via
    task1 >> task2  or  task1.set_downstream(task2).
    """

    def parse(self, path: Path) -> list[DAGDefinition]:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.warning(f"Cannot read Airflow file {path}: {exc}")
            return []

        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            logger.warning(f"Syntax error in {path}: {exc}")
            return []

        dags: list[DAGDefinition] = []

        # Find DAG(...) instantiations
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = self._get_call_name(node)
                if func_name in ("DAG", "dag"):
                    dag_id = self._extract_dag_id(node)
                    if dag_id:
                        dags.append(
                            DAGDefinition(
                                dag_id=dag_id,
                                source_file=str(path),
                                schedule_interval=self._extract_kwarg(node, "schedule_interval"),
                                description=self._extract_kwarg(node, "description"),
                            )
                        )

        if not dags:
            # Fallback: try to detect dag_id from file name
            dags.append(DAGDefinition(dag_id=path.stem, source_file=str(path)))

        # Find task assignments and operators
        tasks = self._extract_tasks(tree, str(path))

        # Assign all tasks to the first DAG found (simplification for multi-DAG files)
        for dag in dags:
            dag.tasks = tasks

        # Parse dependency edges from >> / << operators
        edges = self._extract_dependency_edges(tree, source)
        self._apply_edges(tasks, edges)

        return dags

    def _get_call_name(self, node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return ""

    def _extract_dag_id(self, node: ast.Call) -> Optional[str]:
        # First positional arg or dag_id kwarg
        if node.args and isinstance(node.args[0], ast.Constant):
            return str(node.args[0].value)
        for kw in node.keywords:
            if kw.arg == "dag_id" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
        return None

    def _extract_kwarg(self, node: ast.Call, key: str) -> Optional[str]:
        for kw in node.keywords:
            if kw.arg == key and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
        return None

    def _extract_tasks(self, tree: ast.AST, source_file: str) -> list[DAGTask]:
        tasks: list[DAGTask] = []
        operator_names = {
            "PythonOperator", "BashOperator", "SQLExecuteQueryOperator",
            "PostgresOperator", "BigQueryOperator", "SparkSubmitOperator",
            "EmailOperator", "HttpOperator", "S3FileTransformOperator",
            "DummyOperator", "EmptyOperator", "BranchPythonOperator",
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                        call = node.value
                        op_name = self._get_call_name(call)
                        if any(op_name.endswith(op) for op in operator_names) or "Operator" in op_name:
                            task_id = self._extract_kwarg(call, "task_id") or target.id
                            tasks.append(
                                DAGTask(
                                    task_id=task_id,
                                    operator=op_name,
                                    source_file=source_file,
                                    line=node.lineno,
                                )
                            )
        return tasks

    def _extract_dependency_edges(
        self, tree: ast.AST, source: str
    ) -> list[tuple[str, str]]:
        """
        Extract task1 >> task2 dependency chains from the AST.
        Returns list of (upstream_var, downstream_var) tuples.
        """
        edges: list[tuple[str, str]] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.BinOp):
                left, right = node.value.left, node.value.right
                if isinstance(node.value.op, ast.RShift):
                    edges.extend(self._flatten_rshift(left, right))

        return edges

    def _flatten_rshift(self, left: ast.expr, right: ast.expr) -> list[tuple[str, str]]:
        lefts = self._collect_names(left)
        rights = self._collect_names(right)
        return [(l, r) for l in lefts for r in rights]

    def _collect_names(self, node: ast.expr) -> list[str]:
        if isinstance(node, ast.Name):
            return [node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            names = []
            for elt in node.elts:
                names.extend(self._collect_names(elt))
            return names
        return []

    def _apply_edges(
        self, tasks: list[DAGTask], edges: list[tuple[str, str]]
    ) -> None:
        task_map = {t.task_id: t for t in tasks}
        # Also build a variable-name → task_id map (best effort)
        for edge in edges:
            up_var, dn_var = edge
            up_task = task_map.get(up_var)
            dn_task = task_map.get(dn_var)
            if up_task and dn_task:
                up_task.downstream_task_ids.append(dn_task.task_id)
                dn_task.upstream_task_ids.append(up_task.task_id)


# ---------------------------------------------------------------------------
# dbt schema.yml parser
# ---------------------------------------------------------------------------


class DBTSchemaParser:
    """
    Parses dbt schema.yml / sources.yml files to extract model metadata,
    column descriptions, tests, and source table definitions.
    """

    def parse_schema(self, path: Path) -> tuple[list[DBTModel], list[DBTSource]]:
        try:
            with path.open(encoding="utf-8") as f:
                data: dict = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning(f"Cannot parse dbt YAML {path}: {exc}")
            return [], []

        models = self._parse_models(data, str(path))
        sources = self._parse_sources(data, str(path))
        return models, sources

    def _parse_models(self, data: dict, source_file: str) -> list[DBTModel]:
        models = []
        for raw in data.get("models", []):
            cols = {
                c["name"]: c.get("description", "")
                for c in raw.get("columns", [])
            }
            tests = [
                str(t) if isinstance(t, str) else list(t.keys())[0]
                for c in raw.get("columns", [])
                for t in c.get("tests", [])
            ]
            models.append(
                DBTModel(
                    name=raw.get("name", "unknown"),
                    source_file=source_file,
                    description=raw.get("description"),
                    columns=cols,
                    tests=tests,
                    tags=raw.get("config", {}).get("tags", []),
                )
            )
        return models

    def _parse_sources(self, data: dict, source_file: str) -> list[DBTSource]:
        sources = []
        for raw in data.get("sources", []):
            tables = [t.get("name", "") for t in raw.get("tables", [])]
            sources.append(
                DBTSource(
                    name=raw.get("name", "unknown"),
                    database=raw.get("database"),
                    schema=raw.get("schema", ""),
                    tables=tables,
                    source_file=source_file,
                )
            )
        return sources


# ---------------------------------------------------------------------------
# dbt project.yml parser (minimal)
# ---------------------------------------------------------------------------


def parse_dbt_project(path: Path) -> dict[str, Any]:
    """Return a dict with top-level dbt project metadata."""
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def is_airflow_dag(path: Path) -> bool:
    """Quick heuristic: does the file contain an Airflow DAG definition?"""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        return "DAG" in content and "airflow" in content.lower()
    except Exception:
        return False


def is_dbt_schema(path: Path) -> bool:
    """Quick heuristic: is this a dbt schema.yml file?"""
    return path.name in ("schema.yml", "sources.yml") or (
        path.suffix in (".yml", ".yaml") and "models" in (path.read_text(errors="replace") if path.exists() else "")
    )