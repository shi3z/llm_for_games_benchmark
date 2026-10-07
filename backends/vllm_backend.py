"""vLLM backend: OpenAI-compatible API plus vLLM-specific extras.

* `disable_thinking: true` in models.yaml -> chat_template_kwargs.enable_thinking=False (Qwen3 etc.)
* For sequential requests (solo=True) prefill/decode times are read from the
  Prometheus /metrics histograms (delta of *_sum before/after the request).
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from .openai_backend import OpenAIBackend

_METRICS = {
    "prefill": "vllm:request_prefill_time_seconds_sum",
    "decode": "vllm:request_decode_time_seconds_sum",
    "queue": "vllm:request_queue_time_seconds_sum",
}


class VLLMBackend(OpenAIBackend):
    name = "vllm"

    def _root(self) -> str:
        return re.sub(r"/v1$", "", self.base_url)

    def _extra_body(self, model):
        body = super()._extra_body(model)
        if self.model_cfg.get("disable_thinking"):
            body.setdefault("chat_template_kwargs", {})["enable_thinking"] = False
        return body

    def _scrape(self) -> dict[str, float] | None:
        try:
            text = self.client.get(self._root() + "/metrics", timeout=3.0).text
        except httpx.HTTPError:
            return None
        out = {k: 0.0 for k in _METRICS}
        for line in text.splitlines():
            for key, metric in _METRICS.items():
                if line.startswith(metric):
                    try:
                        out[key] += float(line.rsplit(" ", 1)[1])
                    except ValueError:
                        pass
        return out

    def generate(self, model, messages, temperature=0.7, max_tokens=128, top_p=0.9, seed=None, **kwargs):
        solo = kwargs.pop("solo", False)
        before = self._scrape() if solo else None
        res = super().generate(model, messages, temperature, max_tokens, top_p, seed, **kwargs)
        if before is not None and res.error is None:
            after = self._scrape()
            if after:
                res.prefill_ms = (after["prefill"] - before["prefill"]) * 1000 or None
                res.decode_ms = (after["decode"] - before["decode"]) * 1000 or res.decode_ms
                res.extra["queue_ms"] = (after["queue"] - before["queue"]) * 1000
                if res.decode_ms and res.output_tokens and res.output_tokens > 1:
                    res.tokens_per_second = (res.output_tokens - 1) / (res.decode_ms / 1000)
        return res

    def info(self, model) -> dict[str, Any]:
        info = super().info(model)
        info["backend"] = "vllm"
        try:
            info["backend_version"] = self.client.get(self._root() + "/version", timeout=5.0).json().get("version")
        except Exception as e:  # noqa: BLE001
            info["version_error"] = str(e)
        return info
