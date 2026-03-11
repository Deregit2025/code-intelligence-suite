"""
Generic LLM client interface.

Routes calls to OpenAI, Anthropic, local Ollama, or OpenRouter depending on
the configured provider.  Enforces the ContextWindowBudget to prevent
runaway API spend.
"""

from __future__ import annotations

import time
from typing import Optional

from src.config import CONFIG
from src.utils.logging_utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Token budget tracker
# ---------------------------------------------------------------------------


class ContextWindowBudget:
    """
    Tracks cumulative token usage across all LLM calls in a single run.
    Raises BudgetExceededError when the hard cap is reached.
    """

    def __init__(self, max_tokens: int) -> None:
        self.max_tokens = max_tokens
        self.used_tokens: int = 0
        self.call_count: int = 0

    def charge(self, tokens: int) -> None:
        self.used_tokens += tokens
        self.call_count += 1
        if self.used_tokens > self.max_tokens:
            raise BudgetExceededError(
                f"Token budget exceeded: {self.used_tokens} > {self.max_tokens}"
            )

    def remaining(self) -> int:
        return max(0, self.max_tokens - self.used_tokens)

    def summary(self) -> dict:
        return {
            "used_tokens": self.used_tokens,
            "max_tokens": self.max_tokens,
            "call_count": self.call_count,
            "remaining": self.remaining(),
        }


class BudgetExceededError(Exception):
    pass


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------


class LLMClient:
    """
    Unified interface for calling LLM providers.

    Usage:
        client = LLMClient()
        response = client.complete("Summarise this module...", tier="bulk")
    """

    def __init__(self) -> None:
        self.budget = ContextWindowBudget(CONFIG.llm.max_tokens_per_run)
        self._openai_client = None
        self._anthropic_client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(
        self,
        prompt: str,
        system: str = "",
        tier: str = "bulk",  # "bulk" | "synthesis"
        max_tokens: int = 1000,
        retries: int = 3,
    ) -> str:
        """
        Send a completion request and return the response text.
        *tier* selects the model (bulk = cheap/fast, synthesis = capable).
        """
        provider, model = self._resolve_model(tier)
        logger.debug(f"LLM call: provider={provider} model={model} tier={tier}")

        for attempt in range(retries):
            try:
                if provider == "openai" or provider == "openrouter":
                    return self._call_openai(prompt, system, model, max_tokens, provider)
                elif provider == "anthropic":
                    return self._call_anthropic(prompt, system, model, max_tokens)
                elif provider == "ollama":
                    from src.llm.local_ollama import call_ollama
                    return call_ollama(prompt, system, model)
                else:
                    raise ValueError(f"Unknown provider: {provider}")
            except BudgetExceededError:
                raise
            except Exception as exc:
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"LLM call failed (attempt {attempt+1}), retrying in {wait}s: {exc}")
                    time.sleep(wait)
                else:
                    logger.error(f"LLM call failed after {retries} attempts: {exc}")
                    return f"[LLM_ERROR: {exc}]"

        return "[LLM_ERROR: max retries exceeded]"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_model(self, tier: str) -> tuple[str, str]:
        if tier == "synthesis":
            return CONFIG.llm.synthesis_provider, CONFIG.llm.synthesis_model
        return CONFIG.llm.bulk_provider, CONFIG.llm.bulk_model

    def _call_openai(
        self,
        prompt: str,
        system: str,
        model: str,
        max_tokens: int,
        provider: str = "openai",
    ) -> str:
        if self._openai_client is None:
            from openai import OpenAI

            kwargs: dict = {}
            if provider == "openrouter":
                kwargs["api_key"] = CONFIG.llm.openrouter_api_key
                kwargs["base_url"] = CONFIG.llm.openrouter_base_url
            else:
                kwargs["api_key"] = CONFIG.llm.openai_api_key

            self._openai_client = OpenAI(**kwargs)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self._openai_client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
        )
        usage = response.usage
        if usage:
            self.budget.charge(usage.total_tokens)
        return response.choices[0].message.content or ""

    def _call_anthropic(
        self, prompt: str, system: str, model: str, max_tokens: int
    ) -> str:
        if self._anthropic_client is None:
            import anthropic

            self._anthropic_client = anthropic.Anthropic(api_key=CONFIG.llm.anthropic_api_key)

        kwargs: dict = {"model": model, "max_tokens": max_tokens}
        if system:
            kwargs["system"] = system
        kwargs["messages"] = [{"role": "user", "content": prompt}]

        response = self._anthropic_client.messages.create(**kwargs)
        usage = response.usage
        if usage:
            self.budget.charge(usage.input_tokens + usage.output_tokens)
        return response.content[0].text if response.content else ""


# Module-level singleton
_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client