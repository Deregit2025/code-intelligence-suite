"""
Orchestrator: wires the four agents in sequence.

Pipeline:
    Surveyor → Hydrologist → Semanticist → Archivist

Also handles:
  - GitHub URL cloning
  - Incremental mode (re-analyse only git-changed files)
  - Cartography output dir setup
  - CartographyTracer initialisation
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel

from src.agents.archivist import Archivist
from src.agents.hydrologist import Hydrologist
from src.agents.semanticist import Semanticist
from src.agents.surveyor import Surveyor
from src.config import CONFIG
from src.graph.knowledge_graph import KnowledgeGraph
from src.utils.git_utils import clone_repo, get_head_hash, get_changed_files_since_hash, is_github_url
from src.utils.logging_utils import get_logger, init_tracer
from src.utils.vector_store_utils import SemanticStore

console = Console()
logger = get_logger(__name__)

# File that stores the last-analysed HEAD hash (for incremental mode)
LAST_RUN_META_FILE = "last_run_meta.json"


class Orchestrator:
    """
    Top-level pipeline controller.

    Usage:
        orch = Orchestrator("https://github.com/dbt-labs/jaffle_shop")
        artifacts = orch.run()
    """

    def __init__(
        self,
        repo_path: str,
        clone_base: Path = Path("/tmp/cartographer_repos"),
        incremental: bool = False,
        static_only: bool = False,
    ) -> None:
        CONFIG.incremental = incremental
        CONFIG.static_only = static_only

        # Resolve repo root
        if is_github_url(repo_path):
            repo_name = repo_path.rstrip("/").split("/")[-1].removesuffix(".git")
            self.repo_root = clone_repo(repo_path, clone_base / repo_name)
        else:
            self.repo_root = Path(repo_path).resolve()
            if not self.repo_root.exists():
                raise FileNotFoundError(f"Repo path not found: {self.repo_root}")

        self.cartography_dir = CONFIG.cartography_dir(self.repo_root)

        # Initialise tracer
        self.tracer = init_tracer(self.cartography_dir / "cartography_trace.jsonl")

        logger.info(f"Repository root: {self.repo_root}")
        logger.info(f"Cartography output: {self.cartography_dir}")

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    def run(self) -> dict[str, str]:
        """Execute the full pipeline and return the artifacts dict."""
        start_time = time.time()

        console.print(
            Panel(
                f"[bold cyan]Brownfield Cartographer[/bold cyan]\n"
                f"Repository: [yellow]{self.repo_root.name}[/yellow]\n"
                f"Mode: {'incremental' if CONFIG.incremental else 'full'} | "
                f"LLM: {'disabled (static-only)' if CONFIG.static_only else 'enabled'}",
                title="🗺️  Starting Analysis",
            )
        )

        self.tracer.log(
            agent="Orchestrator",
            action="analysis_start",
            target=str(self.repo_root),
            metadata={
                "incremental": CONFIG.incremental,
                "static_only": CONFIG.static_only,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        # -- Incremental mode: determine changed files ---------------------
        changed_files: Optional[list[str]] = None
        if CONFIG.incremental:
            changed_files = self._get_incremental_changed_files()
            if changed_files is not None:
                logger.info(f"[Orchestrator] Incremental mode: {len(changed_files)} changed files")
            else:
                logger.info("[Orchestrator] Incremental mode: no previous run found, running full analysis")

        # -- Knowledge graph -----------------------------------------------
        kg = KnowledgeGraph()
        # Store repo_root path on the graph for use by Navigator
        kg.module_graph.G.graph["repo_root"] = str(self.repo_root)

        # -- Agent 1: Surveyor ---------------------------------------------
        console.print("\n[bold]Phase 1/4:[/bold] Surveyor — Static Structure Analysis")
        surveyor = Surveyor(self.repo_root, kg)
        surveyor.run(changed_files=changed_files)
        self._print_phase_summary("Surveyor", kg.module_graph.G.number_of_nodes(), "modules")

        # -- Agent 2: Hydrologist ------------------------------------------
        console.print("\n[bold]Phase 2/4:[/bold] Hydrologist — Data Lineage Analysis")
        hydrologist = Hydrologist(self.repo_root, kg)
        hydrologist.run(changed_files=changed_files)
        self._print_phase_summary(
            "Hydrologist",
            len(kg.lineage_graph.get_dataset_nodes()),
            "datasets",
        )

        # -- Agent 3: Semanticist ------------------------------------------
        if not CONFIG.static_only:
            console.print("\n[bold]Phase 3/4:[/bold] Semanticist — LLM Semantic Analysis")
            semanticist = Semanticist(self.repo_root, kg)
            semanticist.run()
            self._print_phase_summary(
                "Semanticist",
                len(semanticist.purpose_statements),
                "purpose statements",
            )
        else:
            console.print("\n[dim]Phase 3/4: Semanticist — SKIPPED (--static-only)[/dim]")
            semanticist = Semanticist(self.repo_root, kg)

        # -- Build vector store (if semantic analysis ran) -----------------
        if not CONFIG.static_only and semanticist.purpose_statements:
            self._build_vector_store(semanticist.purpose_statements)

        # -- Agent 4: Archivist --------------------------------------------
        console.print("\n[bold]Phase 4/4:[/bold] Archivist — Writing Artifacts")
        archivist = Archivist(
            repo_root=self.repo_root,
            kg=kg,
            cartography_dir=self.cartography_dir,
            day_one_answers=semanticist.day_one_answers,
            purpose_statements=semanticist.purpose_statements,
            domain_clusters=semanticist.domain_clusters,
            drift_flags=semanticist.drift_flags,
        )
        artifacts = archivist.run()

        # -- Save run metadata (for incremental mode) ----------------------
        self._save_run_meta()

        elapsed = time.time() - start_time

        self.tracer.log(
            agent="Orchestrator",
            action="analysis_complete",
            metadata={
                "elapsed_seconds": round(elapsed, 1),
                "artifacts": list(artifacts.keys()),
                "summary": kg.summary(),
            },
        )

        console.print(
            Panel(
                f"[green]✓ Analysis complete in {elapsed:.1f}s[/green]\n\n"
                + "\n".join(f"  • {k}: {v}" for k, v in artifacts.items()),
                title="🗺️  Cartography Complete",
            )
        )

        return artifacts

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _print_phase_summary(self, agent: str, count: int, unit: str) -> None:
        console.print(f"  ✓ [green]{agent}[/green] found [bold]{count}[/bold] {unit}")

    def _build_vector_store(self, purpose_statements: dict[str, str]) -> None:
        store_dir = self.cartography_dir / "semantic_index"
        store = SemanticStore(store_dir)
        items = [
            {"id": path, "document": stmt, "metadata": {"path": path}}
            for path, stmt in purpose_statements.items()
        ]
        logger.info(f"[Orchestrator] Building vector store with {len(items)} entries…")
        store.upsert_batch(items)
        logger.info(f"[Orchestrator] Vector store ready at {store_dir}")

    def _get_incremental_changed_files(self) -> Optional[list[str]]:
        meta_path = self.cartography_dir / LAST_RUN_META_FILE
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text())
            last_hash = meta.get("head_hash")
            if not last_hash:
                return None
            return get_changed_files_since_hash(self.repo_root, last_hash)
        except Exception as exc:
            logger.warning(f"Could not read last run metadata: {exc}")
            return None

    def _save_run_meta(self) -> None:
        meta_path = self.cartography_dir / LAST_RUN_META_FILE
        head_hash = get_head_hash(self.repo_root)
        meta = {
            "head_hash": head_hash,
            "analysed_at": datetime.utcnow().isoformat(),
            "repo_root": str(self.repo_root),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")