"""
Agent 2: The Hydrologist – Data Flow & Lineage Analyst

Builds the DataLineageGraph by merging results from:
  - PythonDataFlowAnalyzer  (pandas / SQLAlchemy / PySpark)
  - SQLLineageAnalyzer      (sqlglot)
  - DAGConfigAnalyzer       (Airflow DAG definitions, dbt schema.yml)
  - NotebookAnalyzer        (Jupyter .ipynb)

Output: populates KnowledgeGraph.lineage_graph
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from src.analyzers.dag_config_parser import (
    AirflowDAGParser,
    DBTSchemaParser,
    is_airflow_dag,
    is_dbt_schema,
    parse_dbt_project,
)
from src.analyzers.python_dataflow import PythonDataFlowAnalyzer, analyze_notebook
from src.analyzers.sql_lineage import extract_lineage_from_file
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.edges import EdgeType
from src.models.nodes import DatasetNode, Language, StorageType, TransformationNode
from src.utils.file_utils import detect_language, iter_repo_files, relative_path
from src.utils.logging_utils import get_logger, get_tracer

logger = get_logger(__name__)


def _make_transform_id(source_file: str, line: int) -> str:
    raw = f"{source_file}:{line}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _ensure_dataset(kg: KnowledgeGraph, name: str, storage_type: StorageType) -> None:
    """Add a DatasetNode to the lineage graph if it doesn't already exist."""
    if name not in kg.lineage_graph.G:
        kg.add_dataset(DatasetNode(name=name, storage_type=storage_type))


class Hydrologist:
    """
    Data lineage analysis agent.
    """

    def __init__(self, repo_root: Path, kg: KnowledgeGraph) -> None:
        self.repo_root = repo_root
        self.kg = kg
        self.tracer = get_tracer()
        self._airflow_parser = AirflowDAGParser()
        self._dbt_schema_parser = DBTSchemaParser()
        self._python_analyzer = PythonDataFlowAnalyzer()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, changed_files: Optional[list[str]] = None) -> None:
        logger.info("[Hydrologist] Building data lineage graph…")

        all_files = list(iter_repo_files(self.repo_root))

        python_files = [f for f in all_files if detect_language(f) == Language.PYTHON]
        sql_files = [f for f in all_files if detect_language(f) == Language.SQL]
        yaml_files = [f for f in all_files if detect_language(f) == Language.YAML]
        notebook_files = [f for f in all_files if detect_language(f) == Language.NOTEBOOK]

        # 1. Python data I/O
        logger.info(f"[Hydrologist] Analysing {len(python_files)} Python files for data I/O…")
        for f in tqdm(python_files, desc="Python dataflow", unit="file"):
            if is_airflow_dag(f):
                self._process_airflow(f)
            else:
                self._process_python(f)

        # 2. SQL lineage
        logger.info(f"[Hydrologist] Analysing {len(sql_files)} SQL files…")
        for f in tqdm(sql_files, desc="SQL lineage", unit="file"):
            self._process_sql(f)

        # 3. YAML configs (dbt schema, Airflow YAML)
        logger.info(f"[Hydrologist] Parsing {len(yaml_files)} YAML config files…")
        for f in tqdm(yaml_files, desc="YAML config", unit="file"):
            if is_dbt_schema(f):
                self._process_dbt_schema(f)

        # 4. Notebooks
        logger.info(f"[Hydrologist] Analysing {len(notebook_files)} notebooks…")
        for f in tqdm(notebook_files, desc="Notebooks", unit="file"):
            self._process_notebook(f)

        # 5. Compute source / sink sets
        self._annotate_sources_sinks()

        logger.info(
            f"[Hydrologist] Done. "
            f"Datasets: {len(self.kg.lineage_graph.get_dataset_nodes())}, "
            f"Transformations: {len(self.kg.lineage_graph.get_transformation_nodes())}"
        )

        if self.tracer:
            self.tracer.log(
                agent="Hydrologist",
                action="lineage_complete",
                metadata={
                    "datasets": len(self.kg.lineage_graph.get_dataset_nodes()),
                    "transformations": len(self.kg.lineage_graph.get_transformation_nodes()),
                    "sources": len(self.kg.lineage_graph.find_sources()),
                    "sinks": len(self.kg.lineage_graph.find_sinks()),
                },
            )

    # ------------------------------------------------------------------
    # Processors
    # ------------------------------------------------------------------

    def _process_python(self, path: Path) -> None:
        rel = relative_path(path, self.repo_root)
        result = self._python_analyzer.analyze(path)

        for op in result.read_ops:
            if op.is_dynamic:
                continue
            _ensure_dataset(self.kg, op.dataset, StorageType.FILE)
            tid = _make_transform_id(rel, op.line)
            transform = TransformationNode(
                node_id=tid,
                source_datasets=[op.dataset],
                target_datasets=[],
                transformation_type=op.framework,
                source_file=rel,
                line_range=(op.line, op.line),
            )
            self.kg.add_transformation(transform)
            self.kg.add_lineage_edge(
                op.dataset, tid, EdgeType.PRODUCES, source_file=rel, line_range=(op.line, op.line)
            )

        for op in result.write_ops:
            if op.is_dynamic:
                continue
            _ensure_dataset(self.kg, op.dataset, StorageType.FILE)
            tid = _make_transform_id(rel, op.line)
            if tid not in self.kg.lineage_graph.G:
                transform = TransformationNode(
                    node_id=tid,
                    source_datasets=[],
                    target_datasets=[op.dataset],
                    transformation_type=op.framework,
                    source_file=rel,
                    line_range=(op.line, op.line),
                )
                self.kg.add_transformation(transform)
            self.kg.add_lineage_edge(
                tid, op.dataset, EdgeType.CONSUMES, source_file=rel, line_range=(op.line, op.line)
            )

    def _process_sql(self, path: Path) -> None:
        rel = relative_path(path, self.repo_root)
        lineage = extract_lineage_from_file(path)

        if lineage.parse_errors and not lineage.input_tables and not lineage.output_tables:
            logger.debug(f"[Hydrologist] SQL parse issues for {rel}: {lineage.parse_errors}")
            return

        tid = _make_transform_id(rel, 0)
        transform = TransformationNode(
            node_id=tid,
            source_datasets=lineage.input_tables,
            target_datasets=lineage.output_tables,
            transformation_type="sql",
            source_file=rel,
            line_range=(1, 1),
        )
        self.kg.add_transformation(transform)

        for inp in lineage.input_tables:
            _ensure_dataset(self.kg, inp, StorageType.TABLE)
            self.kg.add_lineage_edge(inp, tid, EdgeType.PRODUCES, source_file=rel)

        for out in lineage.output_tables:
            _ensure_dataset(self.kg, out, StorageType.TABLE)
            self.kg.add_lineage_edge(tid, out, EdgeType.CONSUMES, source_file=rel)

    def _process_airflow(self, path: Path) -> None:
        rel = relative_path(path, self.repo_root)
        dags = self._airflow_parser.parse(path)

        for dag in dags:
            # ---------- Build a task_id → tid map first ----------
            tid_map: dict[str, str] = {}  # task_id → graph node id
            for task in dag.tasks:
                tid = _make_transform_id(rel, task.line)
                tid_map[task.task_id] = tid

            for task in dag.tasks:
                tid = tid_map[task.task_id]

                # -------------------------------------------------------
                # Determine source and target datasets for this task.
                # Strategy:
                #   - task.table present → it is the *target* (the task
                #     reads into or writes to this table).  SQL operators
                #     (PostgresOperator, BigQueryOperator, SQLExecuteQuery)
                #     treat it as target; others as generic dataset.
                #   - task.sql present and it references a .sql file → the
                #     .sql file itself is noted as a source reference.
                #   - upstream task nodes (via dependency edges) feed INTO
                #     this task; no physical dataset names can be inferred
                #     in that case, so we leave datasets empty and rely on
                #     the task-to-task edges instead.
                # -------------------------------------------------------
                source_ds: list[str] = []
                target_ds: list[str] = []

                is_sql_op = any(
                    kw in task.operator
                    for kw in ("SQL", "Postgres", "BigQuery", "Redshift", "Snowflake", "Mysql")
                )

                if task.table:
                    if is_sql_op:
                        target_ds = [task.table]
                    else:
                        # For non-SQL operators (e.g. S3FileTransformOperator)
                        # treat table/bucket as the target dataset
                        target_ds = [task.table]

                # If there's a SQL file reference, treat it as a source input
                if task.sql and task.sql.endswith(".sql"):
                    sql_ref = task.sql.lstrip("/").lstrip("./")
                    source_ds = [sql_ref]

                transform = TransformationNode(
                    node_id=tid,
                    source_datasets=source_ds,
                    target_datasets=target_ds,
                    transformation_type=f"airflow_{task.operator}",
                    source_file=rel,
                    line_range=(task.line, task.line),
                )
                self.kg.add_transformation(transform)

                # Wire dataset → task edges (PRODUCES: dataset feeds task)
                for ds_name in source_ds:
                    _ensure_dataset(self.kg, ds_name, StorageType.TABLE)
                    self.kg.add_lineage_edge(
                        ds_name, tid, EdgeType.PRODUCES,
                        source_file=rel, line_range=(task.line, task.line)
                    )

                # Wire task → dataset edges (CONSUMES: task writes to dataset)
                for ds_name in target_ds:
                    _ensure_dataset(self.kg, ds_name, StorageType.TABLE)
                    self.kg.add_lineage_edge(
                        tid, ds_name, EdgeType.CONSUMES,
                        source_file=rel, line_range=(task.line, task.line)
                    )

                if self.tracer:
                    self.tracer.log(
                        agent="Hydrologist",
                        action="airflow_task_parsed",
                        target=f"{dag.dag_id}.{task.task_id}",
                        metadata={
                            "source_file": rel,
                            "source_datasets": source_ds,
                            "target_datasets": target_ds,
                            "operator": task.operator,
                        },
                    )

            # -------------------------------------------------------
            # Wire task-to-task dependency edges (>> relationships)
            # These represent the DAG execution order, not data flow,
            # but they are stored as PRODUCES edges between transformation
            # nodes so the lineage graph captures the full pipeline topology.
            # -------------------------------------------------------
            for task in dag.tasks:
                up_tid = tid_map[task.task_id]
                for dn_task_id in task.downstream_task_ids:
                    dn_tid = tid_map.get(dn_task_id)
                    if dn_tid and dn_tid in self.kg.lineage_graph.G:
                        self.kg.add_lineage_edge(
                            up_tid, dn_tid, EdgeType.PRODUCES,
                            source_file=rel, line_range=(0, 0)
                        )

        # -------------------------------------------------------
        # Post-process: back-fill source_datasets / target_datasets
        # on node attributes from actual graph adjacency.
        # This guarantees the node-level arrays always match the edges,
        # even for nodes whose datasets were discovered via task dependencies.
        # -------------------------------------------------------
        G = self.kg.lineage_graph.G
        for dag in dags:
            for task in dag.tasks:
                tid = _make_transform_id(rel, task.line)
                if tid not in G:
                    continue
                node_data = G.nodes[tid]
                # predecessors that are datasets (not other transformation nodes)
                pred_datasets = [
                    p for p in G.predecessors(tid)
                    if G.nodes.get(p, {}).get("node_type") == "dataset"
                ]
                # successors that are datasets
                succ_datasets = [
                    s for s in G.successors(tid)
                    if G.nodes.get(s, {}).get("node_type") == "dataset"
                ]
                if pred_datasets:
                    node_data["source_datasets"] = pred_datasets
                if succ_datasets:
                    node_data["target_datasets"] = succ_datasets


    def _process_dbt_schema(self, path: Path) -> None:
        rel = relative_path(path, self.repo_root)
        models, sources = self._dbt_schema_parser.parse_schema(path)

        for source in sources:
            for table in source.tables:
                full_name = f"{source.schema}.{table}" if source.schema else table
                dataset = DatasetNode(
                    name=full_name,
                    storage_type=StorageType.TABLE,
                    owner=source.name,
                    is_source_of_truth=True,
                )
                if full_name not in self.kg.lineage_graph.G:
                    self.kg.add_dataset(dataset)

        for model in models:
            _ensure_dataset(self.kg, model.name, StorageType.TABLE)

        if self.tracer:
            self.tracer.log(
                agent="Hydrologist",
                action="dbt_schema_parsed",
                target=rel,
                metadata={"models": len(models), "sources": len(sources)},
            )

    def _process_notebook(self, path: Path) -> None:
        result = analyze_notebook(path)
        rel = relative_path(path, self.repo_root)

        for op in result.read_ops:
            if op.is_dynamic:
                continue
            _ensure_dataset(self.kg, op.dataset, StorageType.FILE)

        for op in result.write_ops:
            if op.is_dynamic:
                continue
            _ensure_dataset(self.kg, op.dataset, StorageType.FILE)

    # ------------------------------------------------------------------
    # Source / Sink annotation
    # ------------------------------------------------------------------

    def _annotate_sources_sinks(self) -> None:
        G = self.kg.lineage_graph.G
        for node_name in G.nodes:
            node_data = G.nodes[node_name]
            if node_data.get("node_type") == "dataset":
                node_data["is_source"] = G.in_degree(node_name) == 0
                node_data["is_sink"] = G.out_degree(node_name) == 0

    # ------------------------------------------------------------------
    # Query helpers (exposed for Navigator)
    # ------------------------------------------------------------------

    def blast_radius(self, node_name: str) -> list[str]:
        return self.kg.lineage_graph.blast_radius(node_name)

    def upstream_lineage(self, node_name: str) -> list[str]:
        return self.kg.lineage_graph.upstream_lineage(node_name)