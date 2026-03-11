"""
Ollama local LLM wrapper.
Calls the Ollama REST API at localhost:11434 (default).
"""

from __future__ import annotations

import httpx
import time

from src.config import CONFIG
from src.utils.logging_utils import get_logger

logger = get_logger(__name__)


def call_ollama(
    prompt: str,
    system: str = "",
    model: str | None = None,
    timeout: int = 600,
) -> str:
    model = model or CONFIG.llm.ollama_model
    base_url = CONFIG.llm.ollama_base_url

    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if system:
        payload["system"] = system

    try:
        with httpx.Client(trust_env=False) as client:
            response = client.post(
                f"{base_url}/api/generate",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json().get("response", "")
    except Exception as exc:
        logger.error(f"Ollama call failed: {exc}")
        return f"[OLLAMA_ERROR: {exc}]"


def is_ollama_available(retries: int = 5, delay: float = 2.0) -> bool:
    """
    Waits for Ollama to respond before returning True.
    retries: number of attempts
    delay: seconds between attempts
    """
    base_url = CONFIG.llm.ollama_base_url.replace("localhost", "127.0.0.1")
    for attempt in range(1, retries + 1):
        try:
            with httpx.Client(trust_env=False) as client:
                response = client.get(f"{base_url}/api/tags", timeout=5)
                if response.status_code == 200:
                    logger.info(f"Ollama reachable on attempt {attempt}")
                    return True
        except Exception as exc:
            logger.warning(f"Ollama attempt {attempt} failed: {exc}")
        time.sleep(delay)
    logger.error("Ollama is not reachable after retries")
    return False