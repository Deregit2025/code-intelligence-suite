# 🏗️ Report 2: Architecture (Four-Agent Pipeline & Knowledge Graph)

## 1. System Vision
The **Brownfield Cartographer** is designed as a modular, stateless engine that transforms a raw repository path into a stateful, queryable **NetworkX Knowledge Graph**.

Unlike a simple "context loader" for an LLM, the Cartographer prioritizes **deterministic structural metadata** over generative guessing. We only use an LLM after the graph has been mathematically established.

---

## 2. The Core State Layer: Dual DiGraphs
The heartbeat of the system resides in `src/graph/knowledge_graph.py`. We manage two distinct Directed Graphs (`nx.DiGraph`):

### 2.1 The Module Graph (`ModuleGraph`)
- **Nodes**: `ModuleNode` objects representing every file (Python, SQL, YAML).
- **Edges**: `ImportsEdge` representing file-level dependencies.
- **Metrics**: 
  - **PageRank**: Identifying Architectural Hubs (files imported by many, importing few).
  - **SCC (Strongly Connected Components)**: Detecting circular dependency clusters.
  - **In-Degree/Out-Degree**: Quantifying module "noise" and "importance."

### 2.2 The Data Lineage Graph (`DataLineageGraph`)
- **Nodes**: `DatasetNode` (tables, files, buckets) and `TransformationNode` (Airflow tasks, SQL queries, dbt models).
- **Edges**: `LineageEdge` (PRODUCES and CONSUMES).
- **Analysis**:
  - **BFS Blast Radius**: If an S3 bucket changes, what tasks and derivative tables break downstream?
  - **Upstream Ancestry**: Given a dashboard table, trace back to its origin (source-of-truth).

---

## 3. The Four-Agent Pipeline
The `Orchestrator` runs each agent in a strict sequence to build the graph layered from the ground up.

### 🗺️ Agent 1: The Surveyor (Structural Layer)
**Responsibility**: Building the world's skeleton.
1.  **AST Extraction**: Uses `tree-sitter` (Python, JS, TS) to extract functions, classes, and imports.
2.  **Git Signal Integration**: Computes 30-day change velocity per file to identify "High-Velocity Gaps."
3.  **Graph Construction**: Calculates the mathematical center of the project via PageRank.
4.  **Anomaly Detection**: Flags "Dead-Code Candidates" (nodes with zero in/out degrees).

### 💧 Agent 2: The Hydrologist (Lineage Layer)
**Responsibility**: Mapping the data "plumbing" that standard imports miss.
1.  **Airflow AST Parser**: Specifically detects Airflow tasks, operators, and their `upstream` / `downstream` / `Dataset` relationships.
2.  **SQL Lineage (`sqlglot`)**: Parses internal SQL strings to find `FROM` and `JOIN` dependencies.
3.  **dbt Integration**: Ingests `schema.yml` and `ref()` macros to link models across YAML and SQL files.
4.  **Schema Tracking**: Best-effort schema snapshotting for datasets.

### 🧠 Agent 3: The Semanticist (Intelligence Layer)
**Responsibility**: Adding "What" and "Why" to the nodes.
1.  **Per-Module Purpose Statements**: A lightweight model (Ollama `qwen2.5:0.5b`) scans code to generate a single-sentence "Mission Statement."
2.  **Documentation Drift**: Compares actual implementation to the docstring. If they differ, the node is flagged for high risk.
3.  **Domain Clustering**: Using k-means on embeddings to group modules into "Order Service," "Financial Metrics," etc.
4.  **Day-One Synthesis**: A larger model answers the 5-Question Onboarding Brief.

### 📚 Agent 4: The Archivist (Output Layer)
**Responsibility**: Creating the human-usable interface.
1.  **CODEBASE.md**: A compressed, structured architectural document designed for "LLM-Injection."
2.  **onboarding_brief.md**: The grounded answers with clickable source citations.
3.  **Graph Serialisation**: Dumps the NetworkX graphs to JSON (`module_graph.json`, `lineage_graph.json`).
4.  **Trace Maintenance**: Finalizes the `cartography_trace.jsonl` audit log.

---

## 4. High-Fidelity Pipeline Architecture

```mermaid
graph TD
    %% Entry Point
    Repo([📂 Source Code Repository]) --> ORCH{⚙️ Orchestrator}

    %% Agent 1
    subgraph "1. THE SURVEYOR (Physical Layer)"
        A1[[🗺️ Surveyor Agent]]
        A1_AST[Python/JS tree-sitter AST]
        A1_GIT[Git Velocity & Hotspots]
        A1_PR[PageRank Calculation]
        
        A1 -.-> A1_AST
        A1 -.-> A1_GIT
        A1_AST & A1_GIT --> A1_PR
    end

    ORCH --> A1
    A1_PR --> MG[("🕸️ Module Graph (NetworkX DiGraph)")]

    %% Agent 2
    subgraph "2. THE HYDROLOGIST (Lineage Layer)"
        A2[[💧 Hydrologist Agent]]
        A2_SQL[SQL tree-sitter & sqlglot]
        A2_AF[Airflow Task & Dataset Parser]
        A2_DBT[dbt ref() & schema Mapper]
        
        A2 -.-> A2_SQL
        A2 -.-> A2_AF
        A2 -.-> A2_DBT
    end

    MG --> A2
    A2_SQL & A2_AF & A2_DBT --> LG[("🔗 Data Lineage Graph (NetworkX)")]

    %% Agent 3
    subgraph "3. THE SEMANTICIST (Intelligence Layer)"
        A3[[🧠 Semanticist Agent]]
        A3_LLM[Ollama Local Inference]
        A3_DRIFT[Documentation Drift Logic]
        A3_SUMM[Module Purpose Synthesis]
        
        A3 -.-> A3_LLM
        A3_LLM --> A3_DRIFT
        A3_LLM --> A3_SUMM
    end

    LG --> A3
    A3_SUMM & A3_DRIFT --> KG_DATA[("🧠 Grounded Knowledge Base")]

    %% Agent 4
    subgraph "4. THE ARCHIVIST (Interface Layer)"
        A4[[📚 Archivist Agent]]
        A4_MD[Markdown Template Engine]
        A4_JSON[JSON Serializer]
        A4_BRIEF[Onboarding Brief Generator]
        
        A4 -.-> A4_MD
        A4 -.-> A4_JSON
        A4 -.-> A4_BRIEF
    end

    KG_DATA --> A4
    
    %% Final Output
    A4_MD --> CODE[("📄 CODEBASE.md")]
    A4_JSON --> G_JSON[("📦 graph_data.json")]
    A4_BRIEF --> OB[("💡 onboarding_brief.md")]

    CODE & G_JSON & OB --> OUT([📁 .cartography/ Artifacts])

    %% Styling
    style A1 fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style A2 fill:#e3f2fd,stroke:#0d47a1,stroke-width:2px
    style A3 fill:#f3e5f5,stroke:#4a148c,stroke-width:2px
    style A4 fill:#e8f5e9,stroke:#1b5e20,stroke-width:2px
    style MG fill:#ffffff,stroke:#333333,stroke-width:2px
    style LG fill:#ffffff,stroke:#333333,stroke-width:2px
    style KG_DATA fill:#ffffff,stroke:#333333,stroke-width:2px
```

---

## 5. Engineering Guardrails: Proxy & Performance
One of the most critical architectural decisions was the **Local-First Tiered LLM Strategy**:
- **Bypassing Proxies**: Using `httpx.Client(trust_env=False)` to ensure local Ollama works on corporate Windows machines.
- **Token Budgeting**: A hard cap on token usage per run to prevent costs (though currently utilizing free local models).
- **Incremental Mode**: The Archivist saves the `git HEAD` hash. On the next run, the Surveyor only re-analyzes files changed in the git diff.

---

## 6. Detailed Class Breakdown: KnowledgeGraph
### `ModuleGraph` Methods:
- `find_circular_dependencies()`: Uses `nx.strongly_connected_components`.
- `compute_pagerank()`: Returns importance weights.
- `blast_radius_modules()`: Reversed BFS from target node.

### `DataLineageGraph` Methods:
- `upstream_lineage()`: ancestors of a dataset.
- `find_sources()`: in-degree 0.
- `find_sinks()`: out-degree 0.

---

## 7. Conclusion
The architecture is built for **Scale and Resiliency**. By using NetworkX as the core data structure, we can handle repos with 1,000+ files where a pure "LLM-Summarization" approach would fail due to context window limits.

---
_END OF REPORT 2_
