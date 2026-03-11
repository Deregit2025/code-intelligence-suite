"""
Global configuration for the Brownfield Cartographer.
All paths, LLM settings, and analysis options live here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent
CARTOGRAPHY_DIR_NAME = ".cartography"

# ---------------------------------------------------------------------------
# Pydantic settings model
# ---------------------------------------------------------------------------


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    # Tier 1 – cheap / fast (bulk semantic extraction)
    bulk_model: str = Field(
        default=os.getenv("BULK_LLM_MODEL", "gpt-4o-mini"),
        description="Model used for bulk Purpose Statement generation.",
    )
    bulk_provider: Literal["openai", "anthropic", "ollama", "openrouter"] = Field(
        default=os.getenv("BULK_LLM_PROVIDER", "openai"),  # type: ignore[arg-type]
    )

    # Tier 2 – expensive / smart (synthesis, Day-One answers)
    synthesis_model: str = Field(
        default=os.getenv("SYNTHESIS_LLM_MODEL", "gpt-4o"),
        description="Model used for synthesis tasks (Day-One brief, domain clustering).",
    )
    synthesis_provider: Literal["openai", "anthropic", "ollama", "openrouter"] = Field(
        default=os.getenv("SYNTHESIS_LLM_PROVIDER", "openai"),  # type: ignore[arg-type]
    )

    # Local Ollama
    ollama_base_url: str = Field(default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_model: str = Field(default=os.getenv("OLLAMA_MODEL", "mistral"))

    # OpenRouter
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = Field(default=os.getenv("OPENROUTER_API_KEY", ""))

    # OpenAI
    openai_api_key: str = Field(default=os.getenv("OPENAI_API_KEY", ""))

    # Anthropic
    anthropic_api_key: str = Field(default=os.getenv("ANTHROPIC_API_KEY", ""))

    # Budget guard
    max_tokens_per_run: int = Field(
        default=int(os.getenv("MAX_TOKENS_PER_RUN", "500000")),
        description="Hard cap on total tokens consumed across all LLM calls in one run.",
    )


class AnalysisConfig(BaseModel):
    """Tuning knobs for the analysis pipeline."""

    # Git velocity
    git_velocity_days: int = 30
    high_velocity_top_n: int = 20

    # Dead code
    dead_code_import_threshold: int = 0  # 0 imports → candidate

    # Domain clustering
    domain_cluster_k: int = 6  # k-means k for domain detection

    # Embedding model for vector store
    embedding_model: str = "all-MiniLM-L6-v2"

    # Max file size to attempt AST parsing (bytes)
    max_file_bytes: int = 500_000

    # File extensions to analyse
    python_extensions: list[str] = [".py"]
    sql_extensions: list[str] = [".sql"]
    yaml_extensions: list[str] = [".yaml", ".yml"]
    notebook_extensions: list[str] = [".ipynb"]
    js_extensions: list[str] = [".js", ".ts", ".jsx", ".tsx"]

    # Directories to always skip
    skip_dirs: list[str] = [
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".tox",
        "dist",
        "build",
        "*.egg-info",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".cartography",
    ]


class CartographerConfig(BaseModel):
    """Top-level config composed of sub-configs."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)

    # Output dir relative to the analysed repo root
    output_dir_name: str = CARTOGRAPHY_DIR_NAME

    # Whether to run incremental mode (only re-analyse changed files)
    incremental: bool = False

    # Whether to skip LLM calls entirely (pure static analysis mode)
    static_only: bool = False

    def cartography_dir(self, repo_root: Path) -> Path:
        d = ROOT_DIR / self.output_dir_name
        d.mkdir(parents=True, exist_ok=True)
        return d


# Singleton instance used throughout the codebase.
# Components can import and mutate this before calling agents.
CONFIG = CartographerConfig()