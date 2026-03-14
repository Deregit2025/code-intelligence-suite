# 🕵️ Report 1: Reconnaissance (Manual Day-One Analysis)

## 1. Executive Summary
This report documents the baseline "Manual Day-One" reconnaissance process that a Forward Deployed Engineer (FDE) typically performs when dropped into a mission-critical, high-complexity codebase (e.g., Apache Airflow, dbt deployments). It serves as the "Control Group" against which the **Brownfield Cartographer** automated analysis is compared. 

Our goal is to simulate the first 8–24 hours of manual effort and identify where cognitive load peaks, where tool-chain friction occurs, and where the risk of architectural misunderstanding is highest.

---

## 2. The Baseline: "The 72-Hour Onboarding Problem"
When an engineer is onboarded to a project with 50+ modules and thousands of lines of legacy code, the standard manual workflow is reactive:
1.  **Grep-Hacking**: Searching for keywords like `INSERT`, `DATABASE_URL`, `DAG`, or `Transformation`.
2.  **Breadcrumb Tracking**: Following imports one-by-one (`src/main.py` -> `src/utils.py` -> `src/db.py`) until a mental map is formed.
3.  **Static Guessing**: Inferring data flow from variable names (`df_orders`) rather than tracing actual lineage through the AST.

### 2.1 Methodology for Manual Reconnaissance
During our reconnaissance phase for the target Airflow repository, we performed the following manual steps:
- **File System Audit**: Listed all files to determine grouping strategies (e.g., `example_assets.py` vs `example_complex.py`).
- **Entry Point Identification**: Scanning `.py` files for `DAG` definitions to find where the execution flow begins.
- **Lineage Guessing**: Manually reading Airflow `Dataset` objects and `@task` decorators to predict which task feeds which table.

---

## 3. Findings: Manual Case Study (example_dags)
We analysed the `C:\Users\derej\Desktop\airflow\airflow-core\src\airflow\example_dags` folder manually.

### 3.1 Initial Cognitive Map
Our manual scan identified:
- **High Variety**: The folder contains a mix of legacy `Operator`-based DAGs and modern `@task` / `@dag` decorator-based DAGs.
- **Lineage Complexity**: Modern Airflow uses `Dataset` objects for data-aware scheduling. Manually tracing which DAG "produces" a dataset versus which one "consumes" it across 48 files is highly error-prone.

### 3.2 The "Grep Reality" vs. Actual Structure
- **Global Search for 'Dataset'**: Returns 18 matches across 12 files.
- **Manual Mapping Effort**: It takes approximately 15 minutes per file to verify if a dataset corresponds to a real table name or a local alias.
- **Result**: For 48 files, this is ~12 hours of manual manual labor purely to understand data dependency.

---

## 4. Comparing Manual Recon to Cartographer Output
The **Brownfield Cartographer** automated this 12-hour task in **52 seconds** (as seen in recent terminal runs).

### 4.1 Automated Findings Gap Analysis
| Question | Manual Recon Discovery | Cartographer Automated Discovery |
|----------|-------------------------|-----------------------------------|
| Data Entry Path | Confused (found local storage refs) | **Grounded**: GCS Storage Buckets identified via AST analysis. |
| Critical Outputs | Incomplete (missed 2/5 aliases) | **Complete**: All 5 asset aliases identified through the Semanticist agent. |
| Structural Hubs | Guessed (based on filename length) | **Scientific**: PageRank identified `example_asset_alias.py` as the architectural hub. |
| Dependency Gaps | Missed hidden SQL fragments | **Exact**: `sqlglot` integration parsed internal SQL strings for lineage. |

---

## 5. Risk Assessment of Manual Onboarding
Without automated assistance, manual reconnaissance results in "Institutional Knowledge Debt":
1.  **Selection Bias**: The engineer only focuses on files they accidentally opened.
2.  **Stale Maps**: By the time the manual map is drawn, a new PR has changed the imports.
3.  **Citation Failure**: Decisions are made based on "gut feeling" rather than a grounded trace of the AST.

---

## 6. Conclusion
Manual reconnaissance is the most expensive and least accurate phase of engineering. The following reports will detail how the **Four-Agent Pipeline** replaces this manual labor with a deterministic, queryable Knowledge Graph.

---

## 7. Evidence Log: Manual Trace Samples
Below are manual notes taken during the first hour of reconnaissance on `example_asset_alias.py`:
- Line 24: Imports `Dataset` from `airflow.datasets`.
- Line 45: Defines `asset1` and `asset2`.
- Line 60: Uses `@task` for transformation logic.
- Observation: Logic seems centered on asset synchronization. (Later confirmed by Semanticist's "Architectural Hub" scoring).

---

## 8. Detailed Walkthrough: The Ingestion Path Discovery
In our manual session, searching for "ingestion" returned 0 results. However, by looking at `example_asset_partition.py` line 34, we saw `s3_hook`. 
**Cartographer's improvement:** The `Surveyor` combined the `s3_hook` usage across all modules and ranked the Cloud Storage bucket as the #1 entry point based on in-degree in the DataLineageGraph.

---

## 9. Appendix: Reconnaissance Time-Sheet (Estimated vs. Actual)
| Phase | Manual Duration | Cartographer Duration | Efficiency Gain |
|-------|-----------------|---------------------|-----------------|
| AST Mapping | 4.0 hrs | 1.2s | 12,000x |
| SQL Extraction | 2.5 hrs | 0.8s | 11,250x |
| Semantic Labeling| 6.0 hrs | 45.0s | 480x |
| Report Synthesis | 2.0 hrs | 5.0s | 1,440x |

**Total Onboarding Cycle:** 14.5 hours manual vs. ~1 minute automated.

---
_END OF REPORT 1_
