"""
SQL data lineage extraction using sqlglot.

Supports PostgreSQL, BigQuery, Snowflake, DuckDB, and Spark SQL dialects.
Handles:
  - Plain SELECT / FROM / JOIN chains
  - CTEs (WITH clauses)
  - dbt {{ ref() }} and {{ source() }} macros (pre-processed before parsing)
  - Multi-statement files
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.utils.logging_utils import get_logger

logger = get_logger(__name__)

try:
    import sqlglot
    import sqlglot.expressions as exp

    SQLGLOT_AVAILABLE = True
except ImportError:
    SQLGLOT_AVAILABLE = False
    logger.warning("sqlglot not installed – SQL lineage extraction disabled.")

# Dialects supported (tried in order if dialect is unknown)
DIALECTS = ["duckdb", "bigquery", "snowflake", "spark", "postgres"]


@dataclass
class SQLLineageResult:
    """Lineage extracted from a single SQL file or statement."""

    source_file: str
    dialect: str = "unknown"
    input_tables: list[str] = field(default_factory=list)   # tables read
    output_tables: list[str] = field(default_factory=list)  # tables written (INSERT/CREATE)
    cte_names: list[str] = field(default_factory=list)      # intermediate CTEs
    parse_errors: list[str] = field(default_factory=list)


def _preprocess_dbt(sql: str) -> str:
    """
    Replace dbt Jinja macros with parseable SQL placeholders so that sqlglot
    doesn't choke on {{ ref('table') }} syntax.
    """
    # {{ ref('table_name') }}  →  table_name
    sql = re.sub(r"\{\{\s*ref\(['\"](\w+)['\"]\)\s*\}\}", r"\1", sql)
    # {{ source('schema', 'table') }} → schema__table
    sql = re.sub(r"\{\{\s*source\(['\"](\w+)['\"],\s*['\"](\w+)['\"]\)\s*\}\}", r"\1__\2", sql)
    # Strip remaining Jinja blocks  {{ ... }}  {%  ... %}
    sql = re.sub(r"\{\{[^}]*\}\}", "NULL", sql)
    sql = re.sub(r"\{%[^%]*%\}", "", sql)
    return sql


def _extract_tables_from_select(statement) -> tuple[list[str], list[str]]:
    """
    Return (input_tables, output_tables) from a single sqlglot expression.
    """
    input_tables: list[str] = []
    output_tables: list[str] = []

    # Tables in FROM / JOIN (input)
    for table in statement.find_all(exp.Table):
        name = table.name
        db = table.args.get("db")
        if db:
            name = f"{db}.{name}"
        schema = table.args.get("catalog")
        if schema:
            name = f"{schema}.{name}"
        if name and not name.upper() in ("DUAL", "UNNEST"):
            input_tables.append(name)

    # Output tables: INSERT INTO / CREATE TABLE AS / CREATE VIEW AS
    if isinstance(statement, (exp.Insert, exp.Create)):
        target = statement.find(exp.Table)
        if target:
            output_tables.append(target.name)

    return input_tables, output_tables


def extract_sql_lineage(
    sql: str,
    source_file: str,
    dialect: Optional[str] = None,
) -> SQLLineageResult:
    """
    Parse *sql* text and return a SQLLineageResult with table dependencies.
    Tries multiple dialects if *dialect* is None.
    """
    result = SQLLineageResult(source_file=source_file)

    if not SQLGLOT_AVAILABLE:
        result.parse_errors.append("sqlglot not installed")
        return result

    sql = _preprocess_dbt(sql)

    dialects_to_try = [dialect] if dialect else DIALECTS

    parsed_statements = []
    for d in dialects_to_try:
        try:
            parsed_statements = sqlglot.parse(sql, dialect=d, error_level=sqlglot.ErrorLevel.WARN)
            result.dialect = d
            break
        except Exception as exc:
            result.parse_errors.append(f"dialect={d}: {exc}")

    if not parsed_statements:
        return result

    cte_names: set[str] = set()
    all_inputs: set[str] = set()
    all_outputs: set[str] = set()

    for statement in parsed_statements:
        if statement is None:
            continue

        # Collect CTE names (they are not real tables)
        for cte in statement.find_all(exp.CTE):
            alias = cte.alias
            if alias:
                cte_names.add(alias)

        inputs, outputs = _extract_tables_from_select(statement)
        all_inputs.update(inputs)
        all_outputs.update(outputs)

    # Filter out CTEs from the input list (they're internal, not external sources)
    real_inputs = [t for t in all_inputs if t not in cte_names]

    result.input_tables = sorted(set(real_inputs))
    result.output_tables = sorted(set(all_outputs))
    result.cte_names = sorted(cte_names)

    return result


def extract_lineage_from_file(path: Path) -> SQLLineageResult:
    """
    Convenience wrapper: read a .sql file and extract its lineage.
    """
    try:
        sql = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        r = SQLLineageResult(source_file=str(path))
        r.parse_errors.append(str(exc))
        return r

    # Detect dbt model (no explicit CREATE/INSERT = just a SELECT that defines a model)
    is_dbt_model = "ref(" in sql or "source(" in sql

    result = extract_lineage_from_file_text(sql, str(path))

    # For dbt models the file IS the output table (name = stem of the file)
    if is_dbt_model and not result.output_tables:
        result.output_tables.append(path.stem)

    return result


def extract_lineage_from_file_text(sql: str, source_file: str) -> SQLLineageResult:
    return extract_sql_lineage(sql, source_file=source_file)