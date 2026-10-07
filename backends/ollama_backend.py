"""Ollama native /api/chat backend.

The final stream message reports prompt_eval_count/duration (prefill) and
eval_count/duration (decode) in nanoseconds, so those are server-accurate.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .base import Backend, GenerationResult


class OllamaBackend(Backend):
    name = "ollama"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._think_supported = True

    def _payload(self, model, messages, temperature, max_tokens, top_p, seed, **kwargs):
        options: dict[str, Any] = {"temperature": temperature, "top_p": top_p, "num_predict": max_tokens}
        if seed is not None:
            options["seed"] = seed
        options.update(self.model_cfg.get("options") or {})
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True, "options": options,
                                   "keep_alive": self.model_cfg.get("keep_alive", "10m")}
        if "think" in self.model_cfg:              # e.g. gpt-oss: "low" | "medium" | "high"
            payload["think"] = self.model_cfg["think"]
        elif self.model_cfg.get("disable_thinking") and self._think_supported:
            payload["think"] = False
        return payload

    def generate(self, model, messages, temperature=0.7, max_tokens=128, top_p=0.9, seed=None, **kwargs):
        kwargs.pop("solo", None)
        res = GenerationResult()
        payload = self._payload(model, messages, temperature, max_tokens, top_p, seed, **kwargs)
        t0 = time.perf_counter()
        n_chunks = 0
        try:
            with self.client.stream("POST", self.base_url + "/api/chat", json=payload) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    body = resp.text
                    if "think" in body and "think" in payload:
                        self._think_supported = False  # model has no thinking mode
                        return self.generate(model, messages, temperature, max_tokens, top_p, seed, **kwargs)
                    res.error = f"HTTP {resp.status_code}: {body[:300]}"
                    res.latency_ms = (time.perf_counter() - t0) * 1000
                    return res
                for line in resp.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    now = (time.perf_counter() - t0) * 1000
                    msg = chunk.get("message") or {}
                    content, thinking = msg.get("content"), msg.get("thinking")
                    if content or thinking:
                        n_chunks += 1
                        if res.ttft_any_ms is None:
                            res.ttft_any_ms = now
                    if content:
                        res.raw_text += content
                        if res.ttft_ms is None and res.raw_text.split("</think>")[-1].strip() \
                                and not ("<think>" in res.raw_text and "</think>" not in res.raw_text):
                            res.ttft_ms = now
                    if chunk.get("error"):
                        res.error = chunk["error"]
                    if chunk.get("done"):
                        res.finish_reason = chunk.get("done_reason")
                        res.input_tokens = chunk.get("prompt_eval_count")
                        res.output_tokens = chunk.get("eval_count")
                        if chunk.get("prompt_eval_duration"):
                            res.prefill_ms = chunk["prompt_eval_duration"] / 1e6
                        if chunk.get("eval_duration"):
                            res.decode_ms = chunk["eval_duration"] / 1e6
                            if res.output_tokens:
                                res.tokens_per_second = res.output_tokens / (chunk["eval_duration"] / 1e9)
                        if chunk.get("load_duration"):
                            res.extra["load_ms"] = chunk["load_duration"] / 1e6
        except httpx.HTTPError as e:
            res.error = f"{type(e).__name__}: {e}"
        return self._finalize(res, t0, n_chunks)

    def health(self) -> bool:
        try:
            return self.client.get(self.base_url + "/api/version", timeout=3.0).status_code == 200
        except httpx.HTTPError:
            return False

    def prepare(self, model):
        """Unload other models so VRAM numbers belong to this model only, then load it."""
        if self.model_cfg.get("unload_others", True):
            try:
                for m in self.client.get(self.base_url + "/api/ps").json().get("models", []):
                    if m["name"] != model:
                        self.client.post(self.base_url + "/api/generate", json={"model": m["name"], "keep_alive": 0})
                time.sleep(1.0)
            except httpx.HTTPError:
                pass

    def release(self, model):
        if self.model_cfg.get("unload_after", True):
            try:
                self.client.post(self.base_url + "/api/generate", json={"model": model, "keep_alive": 0})
            except httpx.HTTPError:
                pass

    def resource_info(self, model):
        try:
            for m in self.client.get(self.base_url + "/api/ps", timeout=3.0).json().get("models", []):
                if m["name"] == model or m.get("model") == model:
                    return {"server_vram_mb": m.get("size_vram", 0) / 2**20, "server_size_mb": m.get("size", 0) / 2**20,
                            "context_length": m.get("context_length")}
        except httpx.HTTPError:
            pass
        return {}

    def info(self, model):
        info = super().info(model)
        try:
            info["backend_version"] = self.client.get(self.base_url + "/api/version").json().get("version")
            for m in self.client.get(self.base_url + "/api/tags").json().get("models", []):
                if m["name"] == model:
                    info["model_digest"] = m.get("digest")
                    info["model_details"] = m.get("details")
            show = self.client.post(self.base_url + "/api/show", json={"model": model}).json()
            mi = show.get("model_info") or {}
            info["model_info"] = {k: v for k, v in mi.items() if not isinstance(v, list)}
            info["template_sha_head"] = (show.get("template") or "")[:200]
        except Exception as e:  # noqa: BLE001
            info["info_error"] = str(e)
        return info
