from __future__ import annotations

import os
from typing import Any

from .base import Backend, GenerationResult
from .llamacpp_backend import LlamaCppBackend
from .mock_backend import MockBackend
from .ollama_backend import OllamaBackend
from .openai_backend import OpenAIBackend
from .vllm_backend import VLLMBackend

BACKENDS: dict[str, type[Backend]] = {
    "openai": OpenAIBackend,
    "vllm": VLLMBackend,
    "llamacpp": LlamaCppBackend,
    "llama.cpp": LlamaCppBackend,
    "ollama": OllamaBackend,
    "mock": MockBackend,
}

DEFAULT_URLS = {
    "openai": "http://localhost:8000/v1",
    "vllm": "http://localhost:8000/v1",
    "llamacpp": "http://localhost:8080",
    "llama.cpp": "http://localhost:8080",
    "ollama": "http://localhost:11434",
    "mock": "mock://",
}


def create_backend(cfg: dict[str, Any], timeout: float = 300.0) -> Backend:
    """Build a backend from a models.yaml entry (or the judge section of config.yaml)."""
    kind = cfg.get("backend", "openai")
    if kind not in BACKENDS:
        raise ValueError(f"unknown backend '{kind}' (choose from {sorted(BACKENDS)})")
    api_key = cfg.get("api_key") or (os.environ.get(cfg["api_key_env"]) if cfg.get("api_key_env") else None)
    return BACKENDS[kind](cfg.get("base_url") or DEFAULT_URLS[kind], api_key=api_key, timeout=timeout, model_cfg=cfg)


__all__ = ["Backend", "GenerationResult", "create_backend", "BACKENDS"]
