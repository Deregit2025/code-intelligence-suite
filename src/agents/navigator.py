"""
Agent 5: The Navigator – Query Interface

A LangGraph ReAct agent with four tools that allow both exploratory
investigation and precise structured querying of the knowledge graph.

Tools:
  1. find_implementation(concept)       → Semantic search over Purpose Statements
  2. trace_lineage(dataset, direction)  → Graph traversal for upstream/downstream
  3. blast_radius(module_path)          → Dependency impact analysis
  4. explain_module(path)               → Generative explanation of a module

Every answer cites evidence: source file, line range, and analysis method
(static_analysis vs llm_inference).

LangGraph mode:
  Uses ChatOllama (langchain-ollama) so it works entirely locally with
  whatever model is configured in OLLAMA_MODEL / BULK_LLM_MODEL in .env.
  Falls back to a fast direct-dispatch router if LangGraph is not installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from src.config import CONFIG
from src.graph.graph_serializers import load_knowledge_graph
from src.graph.knowledge_graph import KnowledgeGraph
from src.llm.llm_client import get_llm_client
from src.utils.logging_utils import get_logger, get_tracer
from src.utils.vector_store_utils import SemanticStore

logger = get_logger(__name__)


class NavigatorTools:
    """
    Tool implementations used by the Navigator agent.
    Can be used standalone or wired into a LangGraph agent.
    """

    def __init__(self, kg: KnowledgeGraph, semantic_store: Optional[SemanticStore] = None) -> None:
        self.kg = kg
        self.semantic_store = semantic_store
        self._client = get_llm_client()
        self.tracer = get_tracer()

    # ------------------------------------------------------------------
    # Tool 1: find_implementation
    # ------------------------------------------------------------------

    def find_implementation(self, concept: str) -> dict[str, Any]:
        """
        Semantic search: 'Where is the revenue calculation logic?'
        Returns top-k modules whose purpose statement is semantically similar.
        """
        result = {"concept": concept, "matches": [], "evidence_source": "semantic_search"}

        if self.semantic_store and self.semantic_store.count() > 0:
            hits = self.semantic_store.query(concept, n_results=5)
            result["matches"] = [
                {
                    "path": h["id"],
                    "purpose": h["document"],
                    "distance": h["distance"],
                    "evidence_source": "vector_search",
                }
                for h in hits
            ]
        else:
            # Fallback: keyword search over node purpose_statements in graph
            G = self.kg.module_graph.G
            keyword = concept.lower()
            matches = []
            for path, data in G.nodes(data=True):
                purpose = (data.get("purpose_statement") or "").lower()
                if keyword in purpose or keyword in path.lower():
                    matches.append(
                        {
                            "path": path,
                            "purpose": data.get("purpose_statement", ""),
                            "distance": 0.0,
                            "evidence_source": "keyword_fallback",
                        }
                    )
            result["matches"] = matches[:5]
            result["evidence_source"] = "keyword_search_fallback"

        if self.tracer:
            self.tracer.log(
                agent="Navigator",
                action="find_implementation",
                target=concept,
                evidence_source=result["evidence_source"],
            )

        return result

    # ------------------------------------------------------------------
    # Tool 2: trace_lineage
    # ------------------------------------------------------------------

    def trace_lineage(self, dataset: str, direction: str = "upstream") -> dict[str, Any]:
        """
        Graph traversal: 'What produces the daily_active_users table?'

        direction: "upstream" | "downstream" | "both"
        """
        lg = self.kg.lineage_graph

        if dataset not in lg.G:
            # Try partial match
            candidates = [n for n in lg.G.nodes if dataset.lower() in n.lower()]
            if not candidates:
                return {"dataset": dataset, "error": "Dataset not found in lineage graph."}
            dataset = candidates[0]

        result: dict[str, Any] = {
            "dataset": dataset,
            "direction": direction,
            "evidence_source": "static_analysis",
        }

        if direction in ("upstream", "both"):
            upstream = lg.upstream_lineage(dataset)
            result["upstream"] = self._enrich_lineage_nodes(upstream)

        if direction in ("downstream", "both"):
            downstream = lg.blast_radius(dataset)
            result["downstream"] = self._enrich_lineage_nodes(downstream)

        if self.tracer:
            self.tracer.log(
                agent="Navigator",
                action="trace_lineage",
                target=dataset,
                evidence_source="static_analysis",
                metadata={"direction": direction},
            )

        return result

    def _enrich_lineage_nodes(self, node_names: list[str]) -> list[dict]:
        G = self.kg.lineage_graph.G
        enriched = []
        for name in node_names:
            data = G.nodes.get(name, {})
            enriched.append(
                {
                    "name": name,
                    "node_type": data.get("node_type", "unknown"),
                    "source_file": data.get("source_file", ""),
                    "line_range": data.get("line_range", (0, 0)),
                    "transformation_type": data.get("transformation_type", ""),
                }
            )
        return enriched

    # ------------------------------------------------------------------
    # Tool 3: blast_radius
    # ------------------------------------------------------------------

    def blast_radius(self, module_path: str) -> dict[str, Any]:
        """
        Impact analysis: 'What breaks if I change src/transforms/revenue.py?'
        Returns all downstream modules AND datasets affected.
        """
        result: dict[str, Any] = {
            "module": module_path,
            "evidence_source": "static_analysis",
            "affected_modules": [],
            "affected_datasets": [],
        }

        # Module-level blast radius
        affected_modules = self.kg.module_graph.blast_radius_modules(module_path)
        result["affected_modules"] = [
            {
                "path": m,
                "purpose": self.kg.module_graph.G.nodes.get(m, {}).get("purpose_statement", ""),
                "evidence_source": "static_analysis",
            }
            for m in affected_modules
        ]

        # Dataset-level blast radius (if this module appears in lineage graph)
        lg = self.kg.lineage_graph
        if module_path in lg.G:
            affected_datasets = lg.blast_radius(module_path)
            result["affected_datasets"] = affected_datasets

        if self.tracer:
            self.tracer.log(
                agent="Navigator",
                action="blast_radius",
                target=module_path,
                evidence_source="static_analysis",
                metadata={
                    "affected_module_count": len(affected_modules),
                },
            )

        return result

    # ------------------------------------------------------------------
    # Tool 4: explain_module
    # ------------------------------------------------------------------

    def explain_module(self, path: str) -> dict[str, Any]:
        """
        Generative explanation: 'Explain what src/ingestion/kafka_consumer.py does'
        Uses existing purpose statement if available; generates fresh if not.
        """
        G = self.kg.module_graph.G
        node_data = G.nodes.get(path, {})

        existing_purpose = node_data.get("purpose_statement", "")
        domain = node_data.get("domain_cluster", "Unknown")
        pagerank = node_data.get("pagerank_score", 0.0)
        velocity = node_data.get("change_velocity_30d", 0)
        imports = node_data.get("imports", [])
        exported = node_data.get("exported_symbols", [])
        drift = node_data.get("docstring_drift", False)

        if existing_purpose:
            explanation = existing_purpose
            evidence_source = "llm_inference"
        else:
            # Try to read the file and generate on the fly
            full_path = Path(self.kg.module_graph.G.graph.get("repo_root", "")) / path
            if full_path.exists():
                try:
                    source = full_path.read_text(encoding="utf-8", errors="replace")[:4000]
                    prompt = f"""Briefly explain what this module does in 2-3 sentences, focused on its business purpose:

File: {path}
```python
{source}
```
"""
                    explanation = self._client.complete(prompt, tier="bulk", max_tokens=200)
                    evidence_source = "llm_inference"
                except Exception as exc:
                    explanation = f"(Could not generate explanation: {exc})"
                    evidence_source = "error"
            else:
                explanation = "(Module not found in repository)"
                evidence_source = "error"

        result = {
            "path": path,
            "explanation": explanation,
            "domain": domain,
            "pagerank_score": pagerank,
            "change_velocity_30d": velocity,
            "imports": imports[:10],
            "exported_symbols": exported[:10],
            "documentation_drift": drift,
            "evidence_source": evidence_source,
        }

        # Lineage context
        lg = self.kg.lineage_graph
        if path in lg.G:
            result["upstream_datasets"] = lg.upstream_lineage(path)[:5]
            result["downstream_datasets"] = lg.blast_radius(path)[:5]

        if self.tracer:
            self.tracer.log(
                agent="Navigator",
                action="explain_module",
                target=path,
                evidence_source=evidence_source,
            )

        return result


class Navigator:
    """
    Interactive query interface.

    In full LangGraph mode, wraps NavigatorTools as LangChain tools and runs
    a ReAct loop powered by ChatOllama (local, no API key required).
    Falls back to a simple direct-dispatch mode if LangGraph / langchain-ollama
    is unavailable.
    """

    def __init__(self, cartography_dir: Path) -> None:
        self.kg = load_knowledge_graph(cartography_dir)
        semantic_dir = cartography_dir / "semantic_index"
        self.semantic_store = SemanticStore(semantic_dir) if semantic_dir.exists() else None
        self.tools = NavigatorTools(self.kg, self.semantic_store)
        self._client = get_llm_client()

    # ------------------------------------------------------------------
    # Direct dispatch (used by CLI query subcommand without --langgraph)
    # ------------------------------------------------------------------

    def query(self, user_query: str) -> str:
        """
        Route a natural language query to the appropriate tool and return
        a formatted answer.
        """
        query_lower = user_query.lower()

        # Simple intent detection
        if any(kw in query_lower for kw in ["upstream", "produces", "where does", "source", "comes from"]):
            dataset = self._extract_dataset(user_query)
            result = self.tools.trace_lineage(dataset, direction="upstream")
            return self._format_lineage(result)

        elif any(kw in query_lower for kw in ["downstream", "blast radius", "break", "affects", "depends"]):
            module = self._extract_module(user_query)
            result = self.tools.blast_radius(module)
            return self._format_blast_radius(result)

        elif any(kw in query_lower for kw in ["explain", "what does", "what is", "describe"]):
            module = self._extract_module(user_query)
            result = self.tools.explain_module(module)
            return self._format_explain(result)

        else:
            # Default: semantic search
            result = self.tools.find_implementation(user_query)
            return self._format_find(result)

    # ------------------------------------------------------------------
    # LangGraph ReAct agent  (--langgraph CLI flag)
    # ------------------------------------------------------------------

    def run_langgraph_agent(self, user_query: str) -> str:
        """
        Run a full LangGraph ReAct agent backed by ChatOllama.
        Falls back to direct dispatch if LangGraph / langchain-ollama are
        not installed or if Ollama is unreachable.
        """
        try:
            return self._run_with_langgraph(user_query)
        except ImportError as exc:
            logger.warning(f"LangGraph/langchain-ollama not available – using direct dispatch. ({exc})")
            return self.query(user_query)
        except Exception as exc:
            logger.warning(f"LangGraph agent failed – falling back to direct dispatch: {exc}")
            return self.query(user_query)

    def _run_with_langgraph(self, user_query: str) -> str:
        """
        Internal: build the LangGraph ReAct agent with ChatOllama and invoke it.

        Model / URL are driven entirely by CONFIG (read from .env):
          OLLAMA_BASE_URL  → e.g. http://127.0.0.1:11434
          BULK_LLM_MODEL   → e.g. qwen2.5:0.5b
        """
        from langchain_ollama import ChatOllama          # type: ignore
        from langchain_core.tools import tool as lc_tool  # type: ignore
        from langgraph.prebuilt import create_react_agent  # type: ignore

        nav_tools_instance = self.tools

        # ------------------------------------------------------------------
        # Declare the four tools as LangChain @tool functions
        # ------------------------------------------------------------------

        @lc_tool
        def find_implementation(concept: str) -> str:
            """
            Find where a concept or business logic is implemented in the codebase.
            Use this when the user asks 'where is X implemented?' or 'which module
            handles Y?'. Input should be a short description of the concept.
            """
            return json.dumps(
                nav_tools_instance.find_implementation(concept), indent=2, default=str
            )

        @lc_tool
        def trace_lineage(dataset: str, direction: str = "upstream") -> str:
            """
            Trace the data lineage for a dataset.
            Use this when the user asks 'what produces X?' (upstream) or
            'what does X feed into?' (downstream).
            direction must be one of: 'upstream', 'downstream', or 'both'.
            """
            return json.dumps(
                nav_tools_instance.trace_lineage(dataset, direction), indent=2, default=str
            )

        @lc_tool
        def blast_radius(module_path: str) -> str:
            """
            Find what modules and datasets would break if this module changes.
            Use this when the user asks 'what breaks if I change X?' or
            'what is the impact of modifying Y?'.
            Input should be a repo-relative file path, e.g. 'src/utils/file_utils.py'.
            """
            return json.dumps(
                nav_tools_instance.blast_radius(module_path), indent=2, default=str
            )

        @lc_tool
        def explain_module(path: str) -> str:
            """
            Explain what a module does in plain English.
            Use this when the user asks 'explain X', 'what does Y do?', or
            'describe the purpose of Z'.
            Input should be a repo-relative file path, e.g. 'src/agents/surveyor.py'.
            """
            return json.dumps(
                nav_tools_instance.explain_module(path), indent=2, default=str
            )

        # ------------------------------------------------------------------
        # Build ChatOllama from CONFIG (reads BULK_LLM_MODEL and OLLAMA_BASE_URL)
        # ------------------------------------------------------------------
        # Determine model: prefer BULK_LLM_MODEL when provider is ollama,
        # fall back to OLLAMA_MODEL, then 'qwen2.5:0.5b' as hard default.
        if CONFIG.llm.bulk_provider == "ollama":
            model_name = CONFIG.llm.bulk_model
        else:
            model_name = CONFIG.llm.ollama_model or "qwen2.5:0.5b"

        base_url = CONFIG.llm.ollama_base_url  # e.g. http://127.0.0.1:11434

        logger.info(f"[Navigator] LangGraph agent using ChatOllama model={model_name} base_url={base_url}")

        llm = ChatOllama(
            model=model_name,
            base_url=base_url,
            temperature=0,          # Deterministic for tool-calling
            num_predict=512,        # Keep responses concise
        )

        # ------------------------------------------------------------------
        # Assemble and invoke the ReAct agent
        # ------------------------------------------------------------------
        tools_list = [find_implementation, trace_lineage, blast_radius, explain_module]
        agent = create_react_agent(llm, tools_list)

        result = agent.invoke({"messages": [("user", user_query)]})
        messages = result.get("messages", [])
        if messages:
            last = messages[-1]
            # LangChain messages expose content as .content attribute
            content = getattr(last, "content", None) or str(last)
            return content
        return "(No response from LangGraph agent)"

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _format_lineage(self, result: dict) -> str:
        dataset = result.get("dataset", "?")
        upstream = result.get("upstream", [])
        lines = [f"**Lineage for `{dataset}`** (upstream):", ""]
        if not upstream:
            lines.append("No upstream dependencies found.")
        for node in upstream:
            src = node.get("source_file", "")
            lr = node.get("line_range", (0, 0))
            lines.append(f"- `{node['name']}` ({node.get('node_type', '?')})")
            if src:
                lines.append(f"  ↳ evidence: `{src}` lines {lr[0]}-{lr[1]} [static_analysis]")
        return "\n".join(lines)

    def _format_blast_radius(self, result: dict) -> str:
        module = result.get("module", "?")
        modules = result.get("affected_modules", [])
        datasets = result.get("affected_datasets", [])
        lines = [f"**Blast radius of `{module}`:**", ""]
        lines.append(f"Affected modules ({len(modules)}):")
        for m in modules[:10]:
            lines.append(f"  - `{m['path']}`")
        lines.append(f"\nAffected datasets ({len(datasets)}):")
        for d in datasets[:10]:
            lines.append(f"  - `{d}`")
        lines.append("\n[evidence: static_analysis]")
        return "\n".join(lines)

    def _format_explain(self, result: dict) -> str:
        path = result.get("path", "?")
        explanation = result.get("explanation", "")
        domain = result.get("domain", "?")
        velocity = result.get("change_velocity_30d", 0)
        drift = result.get("documentation_drift", False)
        evidence = result.get("evidence_source", "?")
        lines = [
            f"**`{path}`**",
            f"Domain: {domain} | Change velocity (30d): {velocity} commits",
            *([" Documentation drift detected"] if drift else []),
            "",
            explanation,
            "",
            f"[evidence: {evidence}]",
        ]
        return "\n".join(lines)

    def _format_find(self, result: dict) -> str:
        concept = result.get("concept", "?")
        matches = result.get("matches", [])
        lines = [f"**Semantic search: '{concept}'**", ""]
        if not matches:
            lines.append("No matching modules found.")
        for m in matches:
            lines.append(f"- `{m['path']}`")
            if m.get("purpose"):
                lines.append(f"  > {m['purpose'][:200]}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Entity extraction helpers (best-effort)
    # ------------------------------------------------------------------

    def _extract_dataset(self, query: str) -> str:
        import re
        m = re.search(r'["\']([^"\']+)["\']', query)
        if m:
            return m.group(1)
        # Last "word" that looks like a table name
        words = re.findall(r'\b[a-z][a-z_0-9]*\b', query.lower())
        return words[-1] if words else query

    def _extract_module(self, query: str) -> str:
        import re
        # Look for path-like strings
        m = re.search(r'[\w./]+\.py', query)
        if m:
            return m.group(0)
        m = re.search(r'["\']([^"\']+)["\']', query)
        if m:
            return m.group(1)
        return query