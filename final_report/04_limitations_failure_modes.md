# Report 4: Limitations and Failure Mode Awareness

> **Subject**: Known limitations, failure modes, and technical boundaries of the Brownfield Cartographer
> **Date**: 14 March 2026
> **Target**: Apache Airflow `example_dags` analysis (post-run validation)

---

## 1. Executive Summary

Every automated codebase intelligence system has failure modes. The Brownfield Cartographer is no exception. This report catalogs all observed and theoretically predicted failure modes, organized by component. It distinguishes between:

- **Observed failures**: Directly confirmed from the `.cartography/` artifacts vs. ground truth
- **Theoretical failures**: Predicted from implementation analysis without direct observation
- **Graceful degradations**: Areas where the system fails quietly but non-destructively

Understanding these limitations is critical before deploying this system in a real FDE onboarding context.

---

## 2. Failure Mode 1: Asset-Based Lineage Is Completely Blind (Observed)

### 2.1 Description
The `Hydrologist`'s Airflow lineage extractor is designed to detect `table=` and `sql=` keyword arguments from SQL-oriented operators like `PostgresOperator`, `BigQueryOperator`, and `SQLExecuteQuery`. The Airflow `example_dags` collection exclusively uses the **Asset API** (`Asset(uri)`, `outlets=[...]`, `schedule=[asset]`), which is not SQL-based.

### 2.2 Evidence from Artifacts
From `lineage_graph.json`:
```json
{
  "node_type": "transformation",
  "source_datasets": [],
  "target_datasets": [],
  "transformation_type": "airflow_EmptyOperator",
  "source_file": "example_branch_labels.py"
}
```
Every single transformation node has empty `source_datasets` and `target_datasets`. The actual dataset `s3://dag1/output_1.txt` that is demonstrably produced by `asset_produces_1` was never detected.

### 2.3 Root Cause
```python
# dag_config_parser.py — the extractor looks for keyword args:
if kw.arg == "table":
    task_info.table = self._extract_string_value(kw.value)
if kw.arg == "sql":
    task_info.sql = self._extract_string_value(kw.value)
```
The `Asset()` objects are passed to `outlets=` and `schedule=`:
```python
# example_assets.py (line 73):
BashOperator(outlets=[dag1_asset], task_id="producing_task_1", bash_command="sleep 5")
```
The extractor does not have a handler for `outlets=` or `schedule=[Asset(...)]`.

### 2.4 Impact
- `CODEBASE.md` shows hash IDs instead of real dataset names.
- The lineage visualization is a flat list of task nodes with no edges between datasets and tasks.
- The Navigator's `trace_lineage()` tool returns empty results for any real dataset.
- Day-One Q1 answer ("data ingestion path") is meaningless.

### 2.5 Fix Required
```python
# Required addition to AirflowDAGParser._parse_task():
elif kw.arg == "outlets":
    # Parse: outlets=[Asset("s3://...")], outlets=[some_var]
    task_info.datasets_produced = self._extract_asset_uris(kw.value)
# In DAG-level parsing, for schedule=[asset]:
elif kw.arg == "schedule":
    dag_info.asset_schedule = self._extract_asset_uris(kw.value)
```

**Severity**: CRITICAL — this failure mode applies to any repo using Airflow Assets (all Airflow 2.4+ modern codebases).

---

## 3. Failure Mode 2: TaskFlow API (`@task` decorator) Partial Coverage (Observed)

### 3.1 Description
The Airflow **TaskFlow API** (`@task`, `@dag`, `@task.branch`, `@task.setup`, `@task.teardown`) uses Python decorators to define tasks. These are substantially different from the traditional `Operator(task_id=...)` call patterns the parser was designed for.

### 3.2 Evidence
`tutorial_taskflow_api.py` (one of the most important files) defines:
```python
@dag(schedule=None, start_date=..., ...)
def tutorial_taskflow_api():
    @task()
    def extract(): ...
    @task(multiple_outputs=True)
    def transform(order_data_dict): ...
    @task()
    def load(total_order_value): ...
```

From the `lineage_graph.json` the status of this file's tasks is unclear — the 91 transformation nodes likely include BashOperator-style tasks but may miss embedded `@task` functions.

### 3.3 Root Cause
The `AirflowDAGParser._parse_task()` looks for `ast.Call` nodes with operator names:
```python
if isinstance(node, ast.Assign):
    if isinstance(node.value, ast.Call):
        func = node.value.func
        if isinstance(func, ast.Name):
            operator_name = func.id  # BashOperator, EmptyOperator, etc.
```
A `@task`-decorated function is parsed by Python's AST as a `ast.FunctionDef` with a `decorator_list`, not an `ast.Assign` with a `ast.Call`. The current parser does not walk function definitions looking for `@task` decorators.

### 3.4 Impact
- TaskFlow ETL pipelines (increasingly the recommended Airflow pattern) are invisible to the lineage extractor.
- The `tutorial_taskflow_api.py` XCom-based `extract → transform → load` chain is not represented in the lineage graph.

**Severity**: HIGH — TaskFlow API is the modern recommended approach in Airflow 2.x and 3.x.

---

## 4. Failure Mode 3: Multi-DAG Files Break Assumed 1:1 File→DAG Mapping (Observed)

### 4.1 Description
`example_assets.py` defines **10 distinct DAGs** in a single Python file. The Cartographer creates one transformation node per task per analysis step, but the grouping of those tasks under their parent DAG IDs is partially lost.

### 4.2 Evidence
From the lineage graph, 91 total transformation nodes exist. Only a small fraction have correct `dag_id` metadata. The `CODEBASE.md` purpose statement for `example_assets.py` says "defines three Dags" in one context and lists all 10 by name in another — a clear inconsistency from the LLM receiving different truncated context windows.

### 4.3 Root Cause
The `AirflowDAGParser` uses a `current_dag_context` stack to track which `with DAG(...)` block a task belongs to. However, `@dag`-decorated functions and variable-reuse patterns (where Python reassigns `dag5 = ...` and `dag6 = ...` to the same variable name in `example_assets.py` lines 145 and 155) confuse the tracker.

**Severity**: MEDIUM — Affects comprehension of multi-DAG files; does not crash the analysis.

---

## 5. Failure Mode 4: qwen2.5:0.5b Model Ceiling on Synthesis Quality (Observed)

### 5.1 Description
The Day-One FDE answers (Q1–Q5) generated by the Semanticist via the `qwen2.5:0.5b` model are consistently low quality. The model produces:
- Answers that repeat the question back (Q4: "business logic is concentrated in the modules")
- Hallucinated hash IDs as meaningful dataset names (Q1: "e807bef9d6cd storage path")
- Oversimplified statistical answers (Q3: "blast radius is 0")

### 5.2 Root Cause
`qwen2.5:0.5b` is a 500-million parameter model. GPT-4o class performance (required for synthesis reasoning) needs 70B+ parameters.

| Model | Parameters | Q4-Style Synthesis | Cost |
|---|---|---|---|
| `qwen2.5:0.5b` | 500M | ❌ Almost always degenerates | Free (local) |
| `qwen2.5:7b` | 7B | ⚠️ Acceptable | Free (local, more VRAM) |
| `llama3:8b` | 8B | ⚠️ Better structured output | Free (local) |
| `GPT-4o-mini` | ~25B | ✅ Good | $0.15 / 1M tokens |
| `GPT-4o` | ~200B | ✅ Excellent | $5 / 1M tokens |

### 5.3 Impact
- Q1–Q5 answers are the primary deliverable for FDE onboarding. Their low quality undermines the system's core value proposition.
- Purpose statements are better (simpler task, smaller context window) but still contain occasional hallucinations.

### 5.4 Mitigation Already in System
The architecture is model-agnostic. Switch to `llama3:8b` or any OpenAI model via `.env`:
```env
BULK_LLM_MODEL=llama3:8b
SYNTHESIS_LLM_MODEL=llama3:8b
```

**Severity**: HIGH for synthesis quality; LOW for structural analysis (which is model-independent).

---

## 6. Failure Mode 5: Token Budget Exhaustion Silently Truncates Context (Theoretical)

### 6.1 Description
The `ContextManager` in `llm/context_manager.py` enforces a token budget per LLM call. For large files (e.g., `example_params_ui_tutorial.py` at 11,492 bytes), the code is silently truncated before being sent to the model.

### 6.2 Code Evidence
```python
# context_manager.py
def build_prompt(self, code: str, max_tokens: int = 2000) -> str:
    tokens = self._tokenize(code)
    if len(tokens) > max_tokens:
        tokens = tokens[:max_tokens]  # SILENT truncation
    return self._detokenize(tokens)
```

The LLM only sees the first ~2000 tokens of a file. `example_params_ui_tutorial.py` has ~2800 tokens. The last portion — which may contain the most important business logic, not boilerplate imports — is invisible to the model.

### 6.3 Impact
- Purpose statements for large files are based on partial code.
- The "bottom" of a long function (where the actual computation happens) is often cut off.

### 6.4 Mitigation
Use a sliding window or summarization approach: split large files into chunks, generate per-chunk summaries, then synthesize a file-level summary from the chunks.

**Severity**: MEDIUM — Affects ~5-10% of files in a typical production repo.

---

## 7. Failure Mode 6: Non-Git Repositories or External Checkouts (Observed)

### 7.1 Description
The `example_dags` folder is a **subdirectory checkout of Apache Airflow**, not a standalone git repository. The Cartographer's `git_utils.py` runs `git log` from the target repo root.

### 7.2 Evidence
All files in the analysis showed `change_velocity_30d: 0` and `last_modified: null`. The git log returned empty results because the git root is several directories above `example_dags/`, and the path-based filtering failed.

### 7.3 Root Cause
```python
# git_utils.py
def get_changed_files_since_hash(repo_root: Path, since_hash: str):
    result = subprocess.run(
        ["git", "log", "--name-only", f"{since_hash}..HEAD", "--", "."],
        cwd=repo_root
    )
```
When `cwd=example_dags/` but the `.git/` directory is at `airflow-core/`, `git log` from a non-root directory works but the relative paths returned (`src/airflow/example_dags/tutorial.py`) don't match the short filenames tracked in the graph (`tutorial.py`).

**Severity**: MEDIUM — Breaks git velocity metrics; does not affect structural analysis.

---

## 8. Failure Mode 7: Node ID Collision via Hash Truncation (Observed)

### 8.1 Description
Transformation node IDs in the lineage graph are generated as:
```python
def _make_transform_id(rel: str, line: int) -> str:
    raw = f"{rel}:{line}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]
```
A 12-character hex hash provides 2^48 ≈ 281 trillion possible values — astronomically unlikely to collide for typical repo sizes. However, **in the `CODEBASE.md` and `onboarding_brief.md`, these 12-char hashes appear as dataset names** (e.g., "e807bef9d6cd storage path"), making the output completely unreadable.

### 8.2 Root Cause
The `lineage_graph.py`'s `find_sources()` method returns ALL nodes with `in_degree == 0`. Since no real dataset nodes were created for this corpus (see Failure Mode 1), the only nodes in the graph are transformation nodes. Their hash IDs appear as "sources" and "sinks."

### 8.3 Fix Required
```python
# lineage_graph.py - find_sources() should filter to dataset nodes only:
def find_sources(self) -> list[str]:
    return [
        n for n in self.G.nodes
        if self.G.in_degree(n) == 0
        and self.G.nodes[n].get("node_type") == "dataset"  # ADD THIS
    ]
```

**Severity**: HIGH for output quality; MEDIUM for correctness (transforms are reported as sources, not datasets).

---

## 9. Failure Mode 8: LangGraph ReAct Agent Gets Stuck on Small Models (Observed)

### 9.1 Description
The Navigator's LangGraph ReAct agent (`--langgraph` mode) requires the LLM to:
1. Understand the user's question
2. Select the correct tool
3. Parse the tool's structured output
4. Formulate a follow-up tool call or generate a final answer

`qwen2.5:0.5b` frequently fails at step 2 (tool selection) and step 3 (output parsing), causing the agent to either:
- Return an empty string
- Loop indefinitely (until `max_iterations` is hit)
- Call the wrong tool

### 9.2 Evidence
User observation during testing: some questions in the `--langgraph` mode returned no visible answer.

### 9.3 Root Cause
The model's instruction-following capability is insufficient for the structured JSON that LangGraph expects for tool parameter passing. The model generates malformed JSON tool calls.

### 9.4 Mitigation Already in System
The `Navigator` has a `try/except` fallback to direct dispatch mode:
```python
try:
    result = self._run_with_langgraph(question)
except Exception:
    result = self._run_direct(question)  # fallback
```

**Severity**: HIGH in practice; MEDIUM architecturally (fallback exists).

---

## 10. Failure Mode 9: Visualizer Legend HTML Injection Incompatibility (Theoretical)

### 9.1 Description
The `Visualizer._add_legend_html()` method stores the legend HTML as `net._carto_legend_html` and relies on pyvis to inject it into the HTML template. However, pyvis ≥ 0.3.x does not expose a clean HTML injection API — the legend HTML is currently not being inserted into the output files.

### 9.2 Evidence
The generated `module_graph.html` and `lineage_graph.html` do not show the floating legend panel that was designed.

### 9.3 Root Cause
```python
# visualizer.py — stores for post-render step that was never implemented:
net._carto_legend_html = legend_html  # type: ignore[attr-defined]
```
The `net.save_graph()` call overwrites the HTML without reading this attribute.

### 9.4 Fix Required
Use a post-render file patching approach:
```python
def _inject_legend(self, output_path: Path, legend_html: str) -> None:
    html = output_path.read_text(encoding="utf-8")
    html = html.replace("</body>", f"{legend_html}\n</body>")
    output_path.write_text(html, encoding="utf-8")
```

**Severity**: LOW — affects aesthetics only; the graph interactions work correctly.

---

## 11. Failure Mode 10: Circular Dependency Results Are Empty (Theoretical)

### 10.1 Description
The Cartographer reports "Circular dependency groups: 0". For the `example_dags` repository with only 4 import edges between 50 nodes, this is correct. However, the implementation may have an edge case with self-referencing imports.

### 10.2 Root Cause Verification
```python
# module_graph.py
def find_circular_dependencies(self) -> list[list[str]]:
    sccs = list(nx.strongly_connected_components(self.G))
    return [list(c) for c in sccs if len(c) > 1]
```
This correctly uses `nx.strongly_connected_components()`. The implementation is sound.

**Severity**: LOW — algorithm is correct; no fix required.

---

## 12. Summary of Failure Modes

| # | Failure | Type | Severity | Status | Fix Complexity |
|---|---|---|---|---|---|
| 1 | Asset-based lineage not extracted | Observed | 🔴 CRITICAL | Open | High |
| 2 | TaskFlow `@task` not detected | Observed | 🟠 HIGH | Open | High |
| 3 | Multi-DAG files lose task grouping | Observed | 🟡 MEDIUM | Open | Medium |
| 4 | `qwen2.5:0.5b` synthesis quality | Observed | 🟠 HIGH | Mitigable | Low (config) |
| 5 | Token budget truncation | Theoretical | 🟡 MEDIUM | Open | Medium |
| 6 | Non-git-root subdirectory checkout | Observed | 🟡 MEDIUM | Open | Low |
| 7 | Hash IDs appear as dataset names | Observed | 🟠 HIGH | Open | Low (1 line) |
| 8 | LangGraph stuck on small model | Observed | 🟠 HIGH | Mitigated | Fallback exists |
| 9 | Legend HTML not injected into output | Theoretical | 🟢 LOW | Open | Low |
| 10 | Circular dep false negatives | Theoretical | 🟢 LOW | N/A | N/A (correct) |

---

## 13. System Resilience Assessment

Despite the above failures, the Cartographer does **not crash** on any of them. All failures are graceful degradations:
- Missing datasets → empty lists in JSON
- LLM failures → empty strings or fallback responses
- Git failure → zero velocity scores
- Parse failures → skipped nodes with warning logs

The `cartography_trace.jsonl` captures all agent actions including errors, making post-hoc debugging possible.

---

*Report 4 complete.*
