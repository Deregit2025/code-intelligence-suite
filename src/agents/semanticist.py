"""
Agent 3: The Semanticist – LLM-Powered Purpose Analyst

Responsibilities:
  - Generate Purpose Statements for each module (grounded in code, not docstrings)
  - Detect Documentation Drift (LLM purpose ≠ existing docstring)
  - Cluster modules into inferred business domains via k-means on embeddings
  - Answer the Five FDE Day-One Questions using full architectural context
  - Respect ContextWindowBudget: use bulk model for per-module analysis,
    synthesis model only for domain clustering and Day-One answers
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from src.config import CONFIG
from src.graph.knowledge_graph import KnowledgeGraph
from src.llm.context_manager import build_module_prompt
from src.llm.llm_client import BudgetExceededError, get_llm_client
from src.utils.file_utils import iter_repo_files, relative_path, safe_read, detect_language
from src.utils.logging_utils import get_logger, get_tracer
from src.models.nodes import Language

logger = get_logger(__name__)

DAY_ONE_QUESTIONS = {
    "Q1": "What is the primary data ingestion path? (Where does data enter the system?)",
    "Q2": "What are the 3-5 most critical output datasets or endpoints?",
    "Q3": "What is the blast radius if the most critical module fails?",
    "Q4": "Where is the business logic concentrated vs. distributed?",
    "Q5": "What has changed most frequently in the last 90 days (git velocity map)?",
}


class Semanticist:
    """
    LLM-powered semantic analysis agent.
    """

    def __init__(self, repo_root: Path, kg: KnowledgeGraph) -> None:
        self.repo_root = repo_root
        self.kg = kg
        self.tracer = get_tracer()
        self._client = get_llm_client()
        self._purpose_statements: dict[str, str] = {}
        self._drift_flags: list[str] = []
        self._domain_clusters: dict[str, str] = {}
        self._day_one_answers: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        if CONFIG.static_only:
            logger.info("[Semanticist] Skipped (static_only mode).")
            return

        logger.info("[Semanticist] Generating semantic understanding…")

        # 1. Per-module purpose statements
        self._generate_purpose_statements()

        # 2. Domain clustering
        self._cluster_into_domains()

        # 3. Day-One questions
        self._answer_day_one_questions()

        # 4. Write results back to graph nodes
        self._persist_to_graph()

        logger.info(
            f"[Semanticist] Done. "
            f"Statements: {len(self._purpose_statements)}, "
            f"Drift flags: {len(self._drift_flags)}, "
            f"Tokens used: {self._client.budget.used_tokens}"
        )

        if self.tracer:
            self.tracer.log(
                agent="Semanticist",
                action="semantic_analysis_complete",
                evidence_source="llm_inference",
                metadata={
                    "purpose_statements": len(self._purpose_statements),
                    "drift_flags": len(self._drift_flags),
                    "token_budget": self._client.budget.summary(),
                },
            )

    # ------------------------------------------------------------------
    # Purpose statement generation
    # ------------------------------------------------------------------

    def _generate_purpose_statements(self) -> None:
        """Generate a purpose statement for each Python module."""
        python_files = [
            f for f in iter_repo_files(self.repo_root)
            if detect_language(f) == Language.PYTHON
        ]

        for path in tqdm(python_files, desc="Purpose statements", unit="file"):
            rel = relative_path(path, self.repo_root)
            source = safe_read(path)
            if not source:
                continue

            # Extract existing docstring (first triple-quoted string in file)
            existing_docstring = self._extract_module_docstring(source)

            prompt = build_module_prompt(source, rel, existing_docstring)

            try:
                response = self._client.complete(prompt, tier="bulk", max_tokens=300)
            except BudgetExceededError:
                logger.warning("[Semanticist] Token budget exceeded – stopping purpose generation.")
                break

            purpose, drift = self._parse_purpose_response(response)
            self._purpose_statements[rel] = purpose

            if drift:
                self._drift_flags.append(rel)
                if self.tracer:
                    self.tracer.log(
                        agent="Semanticist",
                        action="documentation_drift_detected",
                        target=rel,
                        evidence_source="llm_inference",
                        confidence=0.8,
                        metadata={"drift_description": drift},
                    )

    def _extract_module_docstring(self, source: str) -> Optional[str]:
        """Extract the module-level docstring if present."""
        m = re.match(r'^\s*(?:\'\'\'|""")(.*?)(?:\'\'\'|""")', source, re.DOTALL)
        if m:
            return m.group(1).strip()[:500]
        return None

    def _parse_purpose_response(self, response: str) -> tuple[str, Optional[str]]:
        """Parse the structured LLM response into (purpose, drift_description|None)."""
        purpose = ""
        drift = None

        for line in response.splitlines():
            if line.startswith("PURPOSE:"):
                purpose = line[len("PURPOSE:"):].strip()
            elif line.startswith("DRIFT:"):
                drift_raw = line[len("DRIFT:"):].strip()
                if drift_raw.lower() != "none":
                    drift = drift_raw

        if not purpose:
            # Fallback: use entire response as purpose
            purpose = response.strip()[:300]

        return purpose, drift

    # ------------------------------------------------------------------
    # Domain clustering
    # ------------------------------------------------------------------

    def _cluster_into_domains(self) -> None:
        """
        Embed all purpose statements and run k-means clustering to infer
        business domain boundaries.
        """
        if not self._purpose_statements:
            return

        try:
            from sklearn.cluster import KMeans
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.warning("[Semanticist] sklearn/sentence-transformers not available – skipping domain clustering.")
            return

        k = min(CONFIG.analysis.domain_cluster_k, len(self._purpose_statements))
        if k < 2:
            return

        paths = list(self._purpose_statements.keys())
        statements = [self._purpose_statements[p] for p in paths]

        logger.info(f"[Semanticist] Clustering {len(statements)} modules into {k} domains…")
        model = SentenceTransformer(CONFIG.analysis.embedding_model)
        embeddings = model.encode(statements)

        kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = kmeans.fit_predict(embeddings)

        # Label each cluster by asking the LLM to name it
        clusters: dict[int, list[str]] = {}
        for i, label in enumerate(labels):
            clusters.setdefault(int(label), []).append(statements[i])

        cluster_names: dict[int, str] = {}
        for cluster_id, sample_statements in clusters.items():
            sample = "\n".join(sample_statements[:5])
            name_prompt = f"""Given these module purpose statements from the same codebase cluster:

{sample}

In 2-4 words, what business domain do these modules represent?
Respond with ONLY the domain name, e.g. "Data Ingestion" or "User Authentication".
"""
            try:
                name = self._client.complete(name_prompt, tier="synthesis", max_tokens=20).strip()
                cluster_names[cluster_id] = name
            except BudgetExceededError:
                cluster_names[cluster_id] = f"Domain_{cluster_id}"

        for i, path in enumerate(paths):
            self._domain_clusters[path] = cluster_names.get(int(labels[i]), f"Domain_{labels[i]}")

    # ------------------------------------------------------------------
    # Day-One question answering
    # ------------------------------------------------------------------

    def _answer_day_one_questions(self) -> None:
        """
        Synthesise the full Surveyor + Hydrologist output to answer the
        Five FDE Day-One Questions with evidence citations.
        """
        # Build context summary
        module_graph = self.kg.module_graph
        lineage_graph = self.kg.lineage_graph

        top_modules = module_graph.top_modules_by_pagerank(5)
        sources = lineage_graph.find_sources()[:5]
        sinks = lineage_graph.find_sinks()[:5]
        circular_deps = module_graph.find_circular_dependencies()[:3]
        high_velocity = module_graph.G.graph.get("high_velocity_files", [])[:5]

        # Build a condensed purpose index for the prompt
        purpose_sample = "\n".join(
            f"  {path}: {stmt[:150]}"
            for path, stmt in list(self._purpose_statements.items())[:30]
        )

        context = f"""CODEBASE STRUCTURAL SUMMARY:

Top modules by PageRank (architectural hubs):
{chr(10).join(f"  {p} (score={s:.4f})" for p, s in top_modules)}

Data sources (no upstream producers):
{chr(10).join(f"  {s}" for s in sources) or "  (none detected)"}

Data sinks (terminal outputs):
{chr(10).join(f"  s" for s in sinks) or "  (none detected)"}

Circular dependencies:
{chr(10).join(f"  {g}" for g in circular_deps) or "  (none detected)"}

High-velocity files (most frequently changed):
{chr(10).join(f"  {f}" for f in high_velocity) or "  (none detected)"}

Module purpose sample:
{purpose_sample or "  (not available)"}
"""

        questions_text = "\n".join(
            f"  {qid}: {q}" for qid, q in DAY_ONE_QUESTIONS.items()
        )

        prompt = f"""{context}

You are a senior data engineer conducting a 72-hour brownfield codebase assessment.
Using the structural information above, answer each of the Five FDE Day-One Questions.
For each answer, cite specific evidence: file paths, module names, or dataset names from the summary above.
Be concise but precise. If information is insufficient to answer a question, state what evidence is missing.

QUESTIONS:
{questions_text}

Format your response as:
Q1: <answer with evidence>
Q2: <answer with evidence>
Q3: <answer with evidence>
Q4: <answer with evidence>
Q5: <answer with evidence>
"""

        try:
            response = self._client.complete(prompt, tier="synthesis", max_tokens=800)
            self._parse_day_one_answers(response)
        except BudgetExceededError:
            logger.warning("[Semanticist] Token budget exceeded – Day-One answers skipped.")
        except Exception as exc:
            logger.error(f"[Semanticist] Day-One question answering failed: {exc}")

    def _parse_day_one_answers(self, response: str) -> None:
        for qid in DAY_ONE_QUESTIONS:
            m = re.search(rf"{qid}:\s*(.+?)(?=Q\d:|$)", response, re.DOTALL)
            if m:
                self._day_one_answers[qid] = m.group(1).strip()

    # ------------------------------------------------------------------
    # Persist back to graph
    # ------------------------------------------------------------------

    def _persist_to_graph(self) -> None:
        G = self.kg.module_graph.G
        for path, purpose in self._purpose_statements.items():
            if path in G:
                G.nodes[path]["purpose_statement"] = purpose
            if path in self._domain_clusters:
                domain = self._domain_clusters[path]
                if path in G:
                    G.nodes[path]["domain_cluster"] = domain
            if path in self._drift_flags:
                if path in G:
                    G.nodes[path]["docstring_drift"] = True

        # Store Day-One answers as a graph-level attribute
        G.graph["day_one_answers"] = self._day_one_answers

    # ------------------------------------------------------------------
    # Public accessors (used by Archivist)
    # ------------------------------------------------------------------

    @property
    def purpose_statements(self) -> dict[str, str]:
        return self._purpose_statements

    @property
    def domain_clusters(self) -> dict[str, str]:
        return self._domain_clusters

    @property
    def drift_flags(self) -> list[str]:
        return self._drift_flags

    @property
    def day_one_answers(self) -> dict[str, str]:
        return self._day_one_answers