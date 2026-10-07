"""llama.cpp `llama-server` backend.

Uses the OpenAI-compatible endpoint; the final stream chunk carries
`timings` (prompt_n/prompt_ms/predicted_n/predicted_ms), which gives exact
prefill / decode times and token counts.
"""
from __future__ import annotations

from typing import Any

import httpx

from .openai_backend import OpenAIBackend


class LlamaCppBackend(OpenAIBackend):
    name = "llamacpp"

    def _root(self) -> str:
        return self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url

    def _extra_body(self, model):
        body = super()._extra_body(model)
        if self.model_cfg.get("disable_thinking"):
            body.setdefault("chat_template_kwargs", {})["enable_thinking"] = False
        body.setdefault("timings_per_token", False)
        return body

    def _on_chunk(self, chunk, res):
        t = chunk.get("timings")
        if not t:
            return
        res.prefill_ms = t.get("prompt_ms")
        res.decode_ms = t.get("predicted_ms")
        if t.get("predicted_n") is not None:
            res.output_tokens = t["predicted_n"]
        if t.get("prompt_n") is not None:
            # prompt_n excludes cached tokens; keep usage value if present
            res.extra["prompt_tokens_evaluated"] = t["prompt_n"]
            res.extra["prompt_tokens_cached"] = t.get("cache_n")
            if res.input_tokens is None:
                res.input_tokens = t["prompt_n"] + (t.get("cache_n") or 0)
        if t.get("predicted_per_second"):
            res.tokens_per_second = t["predicted_per_second"]

    def health(self) -> bool:
        try:
            return self.client.get(self._root() + "/health", timeout=3.0).status_code == 200
        except httpx.HTTPError:
            return False

    def info(self, model) -> dict[str, Any]:
        info = super().info(model)
        info["backend"] = "llamacpp"
        try:
            props = self.client.get(self._root() + "/props", timeout=5.0).json()
            info["backend_version"] = props.get("build_info")
            info["model_path"] = props.get("model_path")
            gen = props.get("default_generation_settings") or {}
            info["n_ctx"] = gen.get("n_ctx")
            info["total_slots"] = props.get("total_slots")
        except Exception as e:  # noqa: BLE001
            info["props_error"] = str(e)
        return info
