"""OpenAI-compatible /v1/chat/completions backend (streaming)."""
from __future__ import annotations

import time
from typing import Any

import httpx

from .base import Backend, GenerationResult


class OpenAIBackend(Backend):
    name = "openai"

    def _url(self, path: str) -> str:
        base = self.base_url
        if not base.endswith("/v1"):
            base += "/v1"
        return base + path

    def _extra_body(self, model: str) -> dict[str, Any]:
        return dict(self.model_cfg.get("extra_body") or {})

    def _payload(self, model, messages, temperature, max_tokens, top_p, seed, **kwargs) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if seed is not None:
            payload["seed"] = seed
        if self.model_cfg.get("reasoning_effort"):   # OpenAI-style reasoning models (gpt-oss etc.)
            payload["reasoning_effort"] = self.model_cfg["reasoning_effort"]
        payload.update(self._extra_body(model))
        payload.update(kwargs.get("extra_body") or {})
        return payload

    def _on_chunk(self, chunk: dict[str, Any], res: GenerationResult) -> None:
        """Hook for server-specific fields (llama.cpp timings etc.)."""

    def generate(self, model, messages, temperature=0.7, max_tokens=128, top_p=0.9, seed=None, **kwargs):
        res = GenerationResult()
        payload = self._payload(model, messages, temperature, max_tokens, top_p, seed, **kwargs)
        t0 = time.perf_counter()
        n_chunks = 0
        try:
            with self.client.stream("POST", self._url("/chat/completions"), json=payload) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    res.error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                    res.latency_ms = (time.perf_counter() - t0) * 1000
                    return res
                for chunk in self._iter_sse(resp):
                    now = (time.perf_counter() - t0) * 1000
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        content = delta.get("content")
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        if content or reasoning:
                            n_chunks += 1
                            if res.ttft_any_ms is None:
                                res.ttft_any_ms = now
                        if content:
                            res.raw_text += content
                            # visible TTFT: first content outside a <think> block
                            if res.ttft_ms is None and _visible(res.raw_text):
                                res.ttft_ms = now
                        if choice.get("finish_reason"):
                            res.finish_reason = choice["finish_reason"]
                    usage = chunk.get("usage")
                    if usage:
                        res.input_tokens = usage.get("prompt_tokens")
                        res.output_tokens = usage.get("completion_tokens")
                    self._on_chunk(chunk, res)
        except httpx.HTTPError as e:
            res.error = f"{type(e).__name__}: {e}"
        return self._finalize(res, t0, n_chunks)

    def health(self) -> bool:
        try:
            return self.client.get(self._url("/models"), timeout=3.0).status_code == 200
        except httpx.HTTPError:
            return False

    def info(self, model):
        info = super().info(model)
        try:
            data = self.client.get(self._url("/models"), timeout=5.0).json()
            for m in data.get("data", []):
                if m.get("id") == model:
                    info["model_meta"] = {k: m.get(k) for k in ("id", "root", "owned_by", "created", "max_model_len")}
        except Exception as e:  # noqa: BLE001
            info["info_error"] = str(e)
        return info


def _visible(text: str) -> bool:
    if "<think>" in text and "</think>" not in text:
        return False
    tail = text.split("</think>", 1)[1] if "</think>" in text else text
    return bool(tail.strip())
