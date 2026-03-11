"""
Context window management utilities.

Estimates token counts before calling the LLM and trims content to fit
within a budget, preserving the most important parts.
"""

from __future__ import annotations

from typing import Optional

from src.utils.logging_utils import get_logger

logger = get_logger(__name__)

try:
    import tiktoken

    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False


def estimate_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    """
    Estimate token count for *text*.
    Uses tiktoken when available; falls back to word-count heuristic (1 token ≈ 0.75 words).
    """
    if TIKTOKEN_AVAILABLE:
        try:
            enc = tiktoken.encoding_for_model(model)
            return len(enc.encode(text))
        except Exception:
            pass
    # Fallback
    return int(len(text.split()) / 0.75)


def trim_to_token_budget(
    text: str,
    budget: int,
    model: str = "gpt-4o-mini",
    trim_from: str = "middle",
) -> str:
    """
    Trim *text* so that its token count does not exceed *budget*.

    *trim_from*:
      - "end"    → keep the beginning (default for most prompts)
      - "middle" → keep first and last N/2 tokens (good for long file dumps)
    """
    if estimate_tokens(text, model) <= budget:
        return text

    # Rough character estimate: 1 token ≈ 4 chars
    char_budget = budget * 4

    if trim_from == "end":
        return text[:char_budget] + "\n\n[... trimmed ...]"

    # Middle trim
    half = char_budget // 2
    return text[:half] + "\n\n[... trimmed ...]\n\n" + text[-half:]


def build_module_prompt(
    source_code: str,
    file_path: str,
    existing_docstring: Optional[str] = None,
    max_source_tokens: int = 3000,
) -> str:
    """
    Build the purpose-extraction prompt for a single module.
    Trims the source code to stay within token limits.
    """
    source_trimmed = trim_to_token_budget(source_code, max_source_tokens)
    prompt = f"""You are analysing a source file from a production data engineering codebase.

File: {file_path}

SOURCE CODE:
```
{source_trimmed}
```
"""
    if existing_docstring:
        prompt += f"""
EXISTING DOCSTRING:
{existing_docstring[:500]}
"""

    prompt += """
TASK:
1. Write a PURPOSE STATEMENT: 2-3 sentences describing what this module does in business terms (not implementation details). Focus on: what data it processes, what transformation or action it performs, and what downstream system or use-case it serves.
2. If an existing docstring was provided, state whether it accurately describes the implementation. If there is a meaningful discrepancy, flag it as DOCUMENTATION_DRIFT.

Respond in this exact format:
PURPOSE: <your 2-3 sentence purpose statement>
DRIFT: <"none" | "DOCUMENTATION_DRIFT: <brief description of discrepancy>">
"""
    return prompt