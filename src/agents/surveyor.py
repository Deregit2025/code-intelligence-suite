"""
Agent 1: The Surveyor – Static Structure Analyst

Responsibilities:
  - Walk the repo and analyse every source file with the LanguageRouter
  - Build the module import graph (NetworkX DiGraph)
  - Compute PageRank to identify architectural hubs
  - Detect circular dependencies (SCCs)
  - Compute git change velocity per file
  - Flag dead-code candidates (symbols with 0 references)

Output: populates KnowledgeGraph.module_graph and emits ModuleNode objects
        with structural metadata filled in.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from src.analyzers.tree_sitter_analyzer import analyze_file
from src.config import CONFIG
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.nodes import Language, ModuleNode
from src.utils.file_utils import detect_language, iter_repo_files, relative_path, safe_read
from src.utils.git_utils import compute_velocity
from src.utils.logging_utils import get_logger, get_tracer

logger = get_logger(__name__)


class Surveyor:
    """
    Structural analysis agent.

    Usage:
        surveyor = Surveyor(repo_root, knowledge_graph)
        surveyor.run()
    """

    def __init__(self, repo_root: Path, kg: KnowledgeGraph) -> None:
        self.repo_root = repo_root
        self.kg = kg
        self.tracer = get_tracer()
        self._velocity: Counter = Counter()
        # Map: import module string → resolved repo-relative path
        self._import_resolution_cache: dict[str, Optional[str]] = {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, changed_files: Optional[list[str]] = None) -> None:
        """
        Run the full structural analysis.

        *changed_files*: if provided (incremental mode), re-analyse only
        those files (but keep the rest of the graph intact).
        """
        logger.info("[Surveyor] Starting structural analysis…")

        # 1. Git velocity
        self._velocity = compute_velocity(self.repo_root, days=CONFIG.analysis.git_velocity_days)

        # 2. Walk files
        all_files = list(iter_repo_files(self.repo_root))
        if changed_files:
            files_to_analyse = [
                f for f in all_files
                if relative_path(f, self.repo_root) in changed_files
            ]
            logger.info(f"[Surveyor] Incremental mode: {len(files_to_analyse)} changed files")
        else:
            files_to_analyse = all_files

        # Filter to analysable languages
        analysable = [
            f for f in files_to_analyse
            if detect_language(f) != Language.OTHER
        ]

        logger.info(f"[Surveyor] Analysing {len(analysable)} files…")

        # 3. Per-file analysis
        module_nodes: dict[str, ModuleNode] = {}

        for file_path in tqdm(analysable, desc="Surveyor", unit="file"):
            node = self._analyse_file(file_path)
            if node:
                module_nodes[node.path] = node
                self.kg.add_module(node)

        # 4. Build import edges
        self._build_import_graph(module_nodes)

        # 5. Post-processing graph metrics
        self._enrich_with_graph_metrics(module_nodes)

        # 6. High-velocity files
        self._tag_high_velocity(module_nodes)

        # 7. Dead code candidates
        self._tag_dead_code(module_nodes)

        logger.info(
            f"[Surveyor] Done. "
            f"Modules: {len(module_nodes)}, "
            f"Circular deps: {len(self.kg.module_graph.find_circular_dependencies())}"
        )

        if self.tracer:
            self.tracer.log(
                agent="Surveyor",
                action="analysis_complete",
                metadata={
                    "files_analysed": len(module_nodes),
                    "circular_dep_groups": len(self.kg.module_graph.find_circular_dependencies()),
                },
            )

    # ------------------------------------------------------------------
    # Per-file analysis
    # ------------------------------------------------------------------

    def _analyse_file(self, path: Path) -> Optional[ModuleNode]:
        rel = relative_path(path, self.repo_root)
        language = detect_language(path)
        source = safe_read(path)

        if source is None:
            logger.debug(f"[Surveyor] Skipping unreadable file: {rel}")
            return None

        try:
            result = analyze_file(path, source, language)
        except Exception as exc:
            logger.warning(f"[Surveyor] Parse error for {rel}: {exc}")
            return None

        lines = source.splitlines()
        loc = len([l for l in lines if l.strip()])
        comment_lines = len([l for l in lines if l.strip().startswith("#")])
        comment_ratio = comment_lines / max(loc, 1)

        # Exported symbols = public functions + public classes
        exported = (
            [f.name for f in result.functions if f.is_public]
            + [c.name for c in result.classes]
        )

        # Imports as module strings
        import_strings = [imp.module for imp in result.imports if imp.module]

        node = ModuleNode(
            path=rel,
            language=language,
            imports=import_strings,
            exported_symbols=exported,
            lines_of_code=loc,
            comment_ratio=comment_ratio,
            change_velocity_30d=self._velocity.get(rel, 0),
        )

        return node

    # ------------------------------------------------------------------
    # Import graph construction
    # ------------------------------------------------------------------

    def _build_import_graph(self, module_nodes: dict[str, ModuleNode]) -> None:
        """
        Resolve import strings to known module paths and add edges.
        Unresolvable imports (third-party libs) are silently ignored.
        """
        known_paths = set(module_nodes.keys())

        # Build a lookup: module_stem → full_path
        stem_map: dict[str, list[str]] = {}
        for p in known_paths:
            stem = Path(p).stem
            stem_map.setdefault(stem, []).append(p)
            # Also index by dotted path (e.g. src.utils.file_utils)
            dotted = p.replace("/", ".").removesuffix(".py")
            stem_map.setdefault(dotted, []).append(p)

        for path, node in module_nodes.items():
            for imp_module in node.imports:
                resolved = self._resolve_import(imp_module, stem_map)
                if resolved and resolved != path:
                    self.kg.add_import_edge(path, resolved)

    def _resolve_import(
        self, module_str: str, stem_map: dict[str, list[str]]
    ) -> Optional[str]:
        if module_str in self._import_resolution_cache:
            return self._import_resolution_cache[module_str]

        # Exact dotted match
        if module_str in stem_map:
            result = stem_map[module_str][0]
            self._import_resolution_cache[module_str] = result
            return result

        # Try last component (e.g. "utils" from "src.utils.file_utils")
        last = module_str.split(".")[-1]
        if last in stem_map:
            result = stem_map[last][0]
            self._import_resolution_cache[module_str] = result
            return result

        self._import_resolution_cache[module_str] = None
        return None

    # ------------------------------------------------------------------
    # Graph metric enrichment
    # ------------------------------------------------------------------

    def _enrich_with_graph_metrics(self, module_nodes: dict[str, ModuleNode]) -> None:
        pr = self.kg.module_graph.compute_pagerank()
        G = self.kg.module_graph.G

        for path, node in module_nodes.items():
            if path in G:
                node.pagerank_score = pr.get(path, 0.0)
                node.in_degree = G.in_degree(path)
                node.out_degree = G.out_degree(path)
                # Update node attributes in graph
                G.nodes[path]["pagerank_score"] = node.pagerank_score
                G.nodes[path]["in_degree"] = node.in_degree
                G.nodes[path]["out_degree"] = node.out_degree

        # Store top-10 by PageRank in graph metadata (attached as graph attribute)
        top = self.kg.module_graph.top_modules_by_pagerank(10)
        self.kg.module_graph.G.graph["top_pagerank_modules"] = [p for p, _ in top]
        self.kg.module_graph.G.graph["circular_dependency_groups"] = (
            self.kg.module_graph.find_circular_dependencies()
        )

    def _tag_high_velocity(self, module_nodes: dict[str, ModuleNode]) -> None:
        sorted_by_velocity = sorted(
            module_nodes.values(), key=lambda n: n.change_velocity_30d, reverse=True
        )
        top_n = max(1, len(sorted_by_velocity) // 5)  # top 20%
        high_velocity = [n.path for n in sorted_by_velocity[:top_n]]
        self.kg.module_graph.G.graph["high_velocity_files"] = high_velocity

    def _tag_dead_code(self, module_nodes: dict[str, ModuleNode]) -> None:
        G = self.kg.module_graph.G
        for path, node in module_nodes.items():
            if path in G:
                if G.in_degree(path) == 0 and G.out_degree(path) == 0:
                    node.is_dead_code_candidate = True
                    G.nodes[path]["is_dead_code_candidate"] = True