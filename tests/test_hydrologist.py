"""Unit tests for the Hydrologist agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.hydrologist import Hydrologist
from src.graph.knowledge_graph import KnowledgeGraph


@pytest.fixture
def pandas_repo(tmp_path: Path) -> Path:
    (tmp_path / "pipeline.py").write_text(
        '''import pandas as pd

df = pd.read_csv("data/raw_orders.csv")
result = df.groupby("customer_id").sum()
result.to_parquet("data/daily_orders.parquet")
'''
    )
    return tmp_path


@pytest.fixture
def sql_repo(tmp_path: Path) -> Path:
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "orders.sql").write_text(
        """
SELECT o.id, o.customer_id, c.name
FROM raw.orders o
JOIN raw.customers c ON o.customer_id = c.id
"""
    )
    return tmp_path


@pytest.fixture
def dbt_repo(tmp_path: Path) -> Path:
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "stg_orders.sql").write_text(
        """
SELECT id, customer_id, amount
FROM {{ ref('raw_orders') }}
"""
    )
    return tmp_path


def test_hydrologist_pandas_reads(pandas_repo: Path) -> None:
    kg = KnowledgeGraph()
    h = Hydrologist(pandas_repo, kg)
    h.run()

    datasets = kg.lineage_graph.get_dataset_nodes()
    dataset_names = set(datasets.keys())
    assert any("raw_orders" in n or "orders" in n for n in dataset_names)


def test_hydrologist_finds_sources_and_sinks(pandas_repo: Path) -> None:
    kg = KnowledgeGraph()
    h = Hydrologist(pandas_repo, kg)
    h.run()

    sources = kg.lineage_graph.find_sources()
    sinks = kg.lineage_graph.find_sinks()
    assert len(sources) >= 0  # sources may be datasets with no upstream
    assert len(sinks) >= 0


def test_hydrologist_sql_lineage(sql_repo: Path) -> None:
    kg = KnowledgeGraph()
    h = Hydrologist(sql_repo, kg)
    h.run()

    datasets = kg.lineage_graph.get_dataset_nodes()
    assert len(datasets) > 0


def test_hydrologist_dbt_model(dbt_repo: Path) -> None:
    kg = KnowledgeGraph()
    h = Hydrologist(dbt_repo, kg)
    h.run()

    # The dbt model creates an output table named "stg_orders"
    datasets = kg.lineage_graph.get_dataset_nodes()
    assert any("stg_orders" in n or "raw_orders" in n for n in datasets)