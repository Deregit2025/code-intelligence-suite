"""
Python data-flow analyzer.

Detects pandas, SQLAlchemy, and PySpark read/write operations to build
the data lineage graph for Python source files.

Design:
- Uses tree-sitter AST when available, falls back to regex.
- Gracefully handles dynamic references (f-strings, variable refs) by
  logging them as 'dynamic_reference' rather than failing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.utils.logging_utils import get_logger

logger = get_logger(__name__)

try:
    import tree_sitter_python as tspython
    from tree_sitter import Language as TSLanguage, Parser

    PY_LANGUAGE = TSLanguage(tspython.language())
    TS_AVAILABLE = True
except Exception:
    TS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DataIOOp:
    """A single data read or write operation detected in Python code."""

    operation: str        # e.g. "pd.read_csv", "spark.read.parquet", "df.to_sql"
    direction: str        # "read" | "write"
    framework: str        # "pandas" | "spark" | "sqlalchemy" | "duckdb" | "unknown"
    dataset: str          # table name / file path (may be "DYNAMIC" if unresolvable)
    source_file: str
    line: int
    is_dynamic: bool = False  # True when the dataset ref was a variable / f-string


@dataclass
class PythonDataFlowResult:
    source_file: str
    read_ops: list[DataIOOp] = field(default_factory=list)
    write_ops: list[DataIOOp] = field(default_factory=list)
    parse_error: Optional[str] = None

    @property
    def all_ops(self) -> list[DataIOOp]:
        return self.read_ops + self.write_ops


# ---------------------------------------------------------------------------
# Regex patterns (used as primary approach AND as fallback)
# ---------------------------------------------------------------------------

# Pandas
PANDAS_READ_PATTERNS = [
    (re.compile(r'pd\.read_csv\(["\']([^"\']+)["\']'), "pd.read_csv", "pandas"),
    (re.compile(r'pd\.read_parquet\(["\']([^"\']+)["\']'), "pd.read_parquet", "pandas"),
    (re.compile(r'pd\.read_sql\(["\']([^"\']+)["\']'), "pd.read_sql", "pandas"),
    (re.compile(r'pd\.read_excel\(["\']([^"\']+)["\']'), "pd.read_excel", "pandas"),
    (re.compile(r'pd\.read_json\(["\']([^"\']+)["\']'), "pd.read_json", "pandas"),
    (re.compile(r'pandas\.read_csv\(["\']([^"\']+)["\']'), "pandas.read_csv", "pandas"),
    (re.compile(r'pandas\.read_parquet\(["\']([^"\']+)["\']'), "pandas.read_parquet", "pandas"),
]

PANDAS_WRITE_PATTERNS = [
    (re.compile(r'\.to_csv\(["\']([^"\']+)["\']'), "df.to_csv", "pandas"),
    (re.compile(r'\.to_parquet\(["\']([^"\']+)["\']'), "df.to_parquet", "pandas"),
    (re.compile(r'\.to_sql\(["\']([^"\']+)["\']'), "df.to_sql", "pandas"),
    (re.compile(r'\.to_excel\(["\']([^"\']+)["\']'), "df.to_excel", "pandas"),
    (re.compile(r'\.to_json\(["\']([^"\']+)["\']'), "df.to_json", "pandas"),
]

# PySpark
SPARK_READ_PATTERNS = [
    (re.compile(r'spark\.read\.\w+\(["\']([^"\']+)["\']'), "spark.read", "spark"),
    (re.compile(r'\.read\.format\(["\'][^"\']+["\']\)\.load\(["\']([^"\']+)["\']'), "spark.read.format", "spark"),
    (re.compile(r'spark\.sql\(["\']([^"\']+)["\']'), "spark.sql", "spark"),
    (re.compile(r'\.read\.table\(["\']([^"\']+)["\']'), "spark.read.table", "spark"),
]

SPARK_WRITE_PATTERNS = [
    (re.compile(r'\.write\.\w+\(["\']([^"\']+)["\']'), "spark.write", "spark"),
    (re.compile(r'\.write\.format\(["\'][^"\']+["\']\)\.save\(["\']([^"\']+)["\']'), "spark.write.format", "spark"),
    (re.compile(r'\.write\.saveAsTable\(["\']([^"\']+)["\']'), "spark.write.saveAsTable", "spark"),
    (re.compile(r'\.insertInto\(["\']([^"\']+)["\']'), "spark.insertInto", "spark"),
]

# SQLAlchemy
SQLALCHEMY_PATTERNS = [
    (re.compile(r'engine\.execute\(["\']([^"\']+)["\']'), "sqlalchemy.execute", "sqlalchemy"),
    (re.compile(r'conn\.execute\(["\']([^"\']+)["\']'), "sqlalchemy.execute", "sqlalchemy"),
    (re.compile(r'session\.execute\(["\']([^"\']+)["\']'), "sqlalchemy.execute", "sqlalchemy"),
]

# Dynamic reference detection (f-strings, variable)
DYNAMIC_PATTERNS = [
    re.compile(r'pd\.read_(?:csv|sql|parquet|excel|json)\((?!["\']).+'),
    re.compile(r'spark\.read\.\w+\((?!["\']).+'),
    re.compile(r'\.to_(?:csv|sql|parquet|excel|json)\((?!["\']).+'),
]


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class PythonDataFlowAnalyzer:
    """
    Scans Python source files for data read/write operations and returns
    a PythonDataFlowResult with all detected I/O.
    """

    def analyze(self, path: Path) -> PythonDataFlowResult:
        result = PythonDataFlowResult(source_file=str(path))
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            result.parse_error = str(exc)
            return result

        lines = source.splitlines()

        for i, line in enumerate(lines, start=1):
            # Reads
            for pattern, op_name, framework in (
                PANDAS_READ_PATTERNS + SPARK_READ_PATTERNS + SQLALCHEMY_PATTERNS
            ):
                m = pattern.search(line)
                if m:
                    result.read_ops.append(
                        DataIOOp(
                            operation=op_name,
                            direction="read",
                            framework=framework,
                            dataset=m.group(1),
                            source_file=str(path),
                            line=i,
                        )
                    )

            # Writes
            for pattern, op_name, framework in PANDAS_WRITE_PATTERNS + SPARK_WRITE_PATTERNS:
                m = pattern.search(line)
                if m:
                    result.write_ops.append(
                        DataIOOp(
                            operation=op_name,
                            direction="write",
                            framework=framework,
                            dataset=m.group(1),
                            source_file=str(path),
                            line=i,
                        )
                    )

            # Dynamic references
            for pattern in DYNAMIC_PATTERNS:
                if pattern.search(line) and not any(
                    p.search(line) for p, _, _ in PANDAS_READ_PATTERNS + PANDAS_WRITE_PATTERNS + SPARK_READ_PATTERNS + SPARK_WRITE_PATTERNS
                ):
                    logger.debug(
                        f"{path}:{i} – dynamic reference detected, cannot resolve: {line.strip()}"
                    )
                    result.read_ops.append(
                        DataIOOp(
                            operation="dynamic",
                            direction="read",
                            framework="unknown",
                            dataset="DYNAMIC",
                            source_file=str(path),
                            line=i,
                            is_dynamic=True,
                        )
                    )

        return result


def analyze_notebook(path: Path) -> PythonDataFlowResult:
    """
    Extract data I/O from a Jupyter notebook (.ipynb) by concatenating
    all code cell sources and running the standard analyzer.
    """
    result = PythonDataFlowResult(source_file=str(path))
    try:
        import nbformat

        nb = nbformat.read(str(path), as_version=4)
        code = "\n".join(
            "".join(cell.source)
            for cell in nb.cells
            if cell.cell_type == "code"
        )
        # Write to a temp path-like object for the analyzer
        import tempfile, os

        with tempfile.NamedTemporaryFile(
            suffix=".py", delete=False, mode="w", encoding="utf-8"
        ) as tmp:
            tmp.write(code)
            tmp_path = Path(tmp.name)

        analyzer = PythonDataFlowAnalyzer()
        tmp_result = analyzer.analyze(tmp_path)
        # Rewrite source_file references back to the notebook
        for op in tmp_result.all_ops:
            op.source_file = str(path)
        result.read_ops = tmp_result.read_ops
        result.write_ops = tmp_result.write_ops
        os.unlink(tmp_path)
    except Exception as exc:
        result.parse_error = str(exc)
        logger.warning(f"Failed to analyse notebook {path}: {exc}")

    return result