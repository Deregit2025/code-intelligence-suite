# 🗺️ Brownfield Cartographer

> A multi-agent codebase intelligence system for rapid FDE onboarding in production environments.

## What It Does

The Brownfield Cartographer ingests any local repository path and produces a **living, queryable knowledge graph** of the system's architecture, data flows, and semantic structure — answering the 5 questions every new FDE needs in the first 72 hours.

### Outputs

All outputs are saved to the `.cartography/` folder inside the **Cartographer project directory** (not the target repository).

| Artifact | Description |
|----------|-------------|
| `CODEBASE.md` | Living context file — inject into any AI coding agent for instant architectural awareness |
| `onboarding_brief.md` | Five FDE Day-One answers with evidence citations |
| `module_graph.html` | **NEW**: Interactive D3-powered import graph with physics and search |
| `lineage_graph.html` | **NEW**: Interactive data lineage visualization (sources → transforms → sinks) |
| `module_graph.json` | Module import graph with PageRank, circular deps, git velocity |
| `lineage_graph.json` | Full data lineage DAG (Python + SQL + Airflow + dbt) |
| `cartography_trace.jsonl` | Audit log of every analysis action and evidence source |
| `last_run.json` | Metadata for incremental mode (git HEAD hash) |

---

## Architecture

```
Surveyor          → Static AST analysis (tree-sitter), module import graph, PageRank, git velocity
  ↓
Hydrologist       → Data lineage (pandas/Spark + sqlglot + Airflow/dbt YAML)
  ↓
Semanticist       → LLM purpose statements, documentation drift, domain clustering, Day-One answers
  ↓
Archivist         → CODEBASE.md, onboarding_brief.md, graph serialisation
  ↓
Navigator         → Query interface: find_implementation, trace_lineage, blast_radius, explain_module
```

---

## Installation

```bash
# Clone the repo
git clone https://github.com/your-org/cartographer.git
cd cartographer

# Install with Poetry (recommended)
pip install poetry
poetry install

# Or with pip:
pip install -e ".[dev]"
```

### Environment Variables

Create a `.env` file in the project root. The system supports multiple LLM backends:

#### ✅ Local-First (Recommended — No API Keys Required)

The recommended setup uses **Ollama** with a lightweight model like `qwen2.5:0.5b` for full local, private, zero-cost inference.

```bash
# 1. Install Ollama: https://ollama.com
# 2. Pull a model:
ollama pull qwen2.5:0.5b
```

Then create your `.env`:

```env
# Local Ollama (both tiers use the same small model)
BULK_LLM_PROVIDER=ollama
BULK_LLM_MODEL=qwen2.5:0.5b

SYNTHESIS_LLM_PROVIDER=ollama
SYNTHESIS_LLM_MODEL=qwen2.5:0.5b

OLLAMA_BASE_URL=http://127.0.0.1:11434
```

> **💡 Note on the Tiered LLM Strategy:**  
> The Semanticist uses two tiers of LLM calls:
> - **Bulk tier**: Per-module purpose statement generation (speed-focused, runs once per file)
> - **Synthesis tier**: Higher-level reasoning for Day-One onboarding Q&A and domain clustering
>
> For local inference, using the same lightweight model for both tiers is recommended.  
> For cloud setups, you may use a cheaper model (e.g. `gpt-4o-mini`) for bulk and a stronger one (e.g. `gpt-4o`) for synthesis.

#### ☁️ Cloud LLM (Optional)

```env
# OpenAI
OPENAI_API_KEY=sk-...
BULK_LLM_PROVIDER=openai
BULK_LLM_MODEL=gpt-4o-mini
SYNTHESIS_LLM_PROVIDER=openai
SYNTHESIS_LLM_MODEL=gpt-4o

# Optional: Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Optional: OpenRouter
OPENROUTER_API_KEY=...

# Token budget guard (default: 500,000)
MAX_TOKENS_PER_RUN=500000
```

---

## Usage

### Analyze a Repository

```bash
# Analyze a local path (artifacts saved to THIS project's .cartography/ folder)
poetry run python -m src.cli analyze /path/to/your/repo

# Example: Airflow example DAGs
poetry run python -m src.cli analyze C:\path\to\airflow\example_dags

# Static-only mode (no LLM calls — instant, zero compute)
poetry run python -m src.cli analyze /path/to/repo --static-only

# Incremental mode (re-analyze only git-changed files since last run)
poetry run python -m src.cli analyze /path/to/repo --incremental
```

### Query the Knowledge Graph

```bash
# Interactive LangGraph ReAct Agent (multi-step reasoning)
poetry run python -m src.cli query /path/to/repo --langgraph

# Standard direct query
poetry run python -m src.cli query /path/to/repo "What produces the orders table?"
```

### 🛰️ Interactive Visualization

Render your knowledge graph as a premium, interactive HTML dashboard with physics simulation:

```bash
# Render both Module and Lineage graphs
poetry run python -m src.cli visualize /path/to/repo

# Open automatically in your default browser
poetry run python -m src.cli visualize /path/to/repo --open

# Render only lineage graph
poetry run python -m src.cli visualize /path/to/repo --graph lineage
```

---

## The Four Agents

### Agent 1: Surveyor (Static Structure)
- Multi-language AST parsing via `tree-sitter` (Python, JS/TS)
- Module import graph with **PageRank** to identify Architectural Hubs
- **Circular dependency detection** via Strongly Connected Components (SCC)
- Git velocity analysis: which files change most frequently (the pain points)
- Dead code candidate identification

### Agent 2: Hydrologist (Data Lineage)
- Python data I/O tracking: `pandas`, `PySpark`, `SQLAlchemy`
- SQL lineage: `sqlglot`-parsed `SELECT/FROM/JOIN/CTE` chains
- **Enhanced Airflow Extraction**:
    - Automatically parses `task.table` and `task.sql` operator fields.
    - Resolves cross-task dependencies via `>>` and `set_downstream` syntax.
    - Wires dataset nodes (tables/S3) directly into the transformation graph.
- dbt `schema.yml` / `sources.yml` parsing
- Jupyter notebook data I/O extraction
- `blast_radius()` and `upstream_lineage()` for any dataset node

### Agent 3: Semanticist (LLM Analysis)
- Per-module **Purpose Statements** grounded in actual code, not docstrings
- **Documentation Drift detection**: flags modules where the docstring no longer matches implementation
- Domain clustering via k-means on purpose embeddings
- Five FDE Day-One answers with evidence citations
- Tiered model usage: fast/cheap model for bulk per-file analysis, deeper model for synthesis

### Agent 4: Archivist (Living Context)
- `CODEBASE.md` structured for direct AI coding agent injection
- `onboarding_brief.md` with evidence-grounded Day-One answers
- Serialised module and lineage graphs (JSON)
- Incremental update support via git diff tracking

---

### Agent 5: Navigator (ReAct Reasoning)
- **LangGraph Integration**: Uses a ReAct loop for multi-step graph exploration.
- **Tools**:
    - `find_implementation(concept)`: Semantic vector search via FAISS.
    - `trace_lineage(dataset, direction)`: Traverses the NetworkX lineage graph.
    - `blast_radius(module_path)`: BFS traversal for impact assessment.
    - `explain_module(path)`: Context-aware generative explanation.

---

## 🛰️ Visualization Dashboard

The visualizer generates self-contained `.html` files in `.cartography/`.
- **Module Graph**: Nodes sized by PageRank (Architectural Hubs). Domains color-coded by LLM clustering.
- **Lineage Graph**: Dataset (blue) and Transformation (green/purple) flows via `PRODUCES/CONSUMES` edges.
- **Interactivity**: Zoom, pan, drag, and click any node to highlight its semantic neighborhood.

---

## Running Tests

```bash
poetry run pytest test/ -v
poetry run pytest test/ -v --cov=src --cov-report=term-missing
```

---

## Supported Target Codebases

| Target | Status | Notes |
|--------|--------|-------|
| Apache Airflow example DAGs | ✅ Verified | Pipeline topology + DAG task lineage from Python AST |
| dbt jaffle_shop | ✅ Primary | Full SQL lineage via sqlglot |
| Any Python + SQL repo | ✅ General | Mixed-language lineage |
| Jupyter notebooks | ✅ Supported | Data I/O extraction |

---

## Project Structure

```
cartographer/
├── src/
│   ├── cli.py                    # Typer CLI (analyze + query subcommands)
│   ├── orchestrator.py           # Pipeline controller (chains all four agents)
│   ├── config.py                 # Global configuration + output directory management
│   ├── models/                   # Pydantic schemas (nodes, edges, graphs)
│   ├── analyzers/                # tree-sitter, sqlglot, dag config parsers
│   ├── agents/                   # Surveyor, Hydrologist, Semanticist, Archivist, Navigator
│   ├── graph/                    # NetworkX wrappers + JSON serialisers
│   ├── llm/                      # LLM client, Ollama wrapper (proxy-bypass), context manager
│   └── utils/                    # File, git, logging, vector store utilities
├── .cartography/                 # Auto-generated output artifacts (always written HERE)
├── test/                         # pytest test suite
└── pyproject.toml
```

---

## Key Engineering Decisions

| Decision | Rationale |
|----------|-----------|
| **NetworkX graph** as core state | Enables cheap, deterministic PageRank/BFS algorithms without LLM compute |
| **Local-first LLM via Ollama** | Zero cost, full data privacy, works without internet |
| `httpx.Client(trust_env=False)` | Bypasses Windows/corporate proxy interference for local Ollama connections |
| **Static analysis before LLM** | Only targeted code chunks sent to LLM — prevents context-window exhaustion |
| **`--static-only` mode** | Full graph builds without any LLM — resilient fallback when compute is limited |
| **Incremental analysis** | Tracks `git HEAD` hash; only re-analyses changed files on subsequent runs |
| **`cartography_trace.jsonl`** | Deterministic audit log of all AI decisions with evidence source ratings |

---

## License

MIT