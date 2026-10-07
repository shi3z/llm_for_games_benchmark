"""Common backend interface.

Every backend exposes the same call:

    result = backend.generate(model=..., messages=[...], temperature=0.7, max_tokens=128)

and returns a GenerationResult with timing fields filled in as far as the
server allows (TTFT is always measured client-side from the stream).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator

import httpx

THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.S)


@dataclass
class GenerationResult:
    text: str = ""
    raw_text: str = ""                 # before <think> stripping
    ttft_ms: float | None = None       # first *visible* token
    ttft_any_ms: float | None = None   # first token of any kind (incl. reasoning)
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tokens_per_second: float | None = None  # decode speed
    prefill_ms: float | None = None    # server-reported when available
    decode_ms: float | None = None
    finish_reason: str | None = None
    token_count_source: str = "server"  # server | chunks
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def strip_thinking(text: str) -> str:
    text = THINK_RE.sub("", text)
    # Some templates emit only the closing tag.
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()


class Backend:
    """Base class. Subclasses implement `_stream` or override `generate`."""

    name = "base"

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 300.0,
                 model_cfg: dict[str, Any] | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_cfg = model_cfg or {}
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), headers=headers,
                                   limits=httpx.Limits(max_connections=64, max_keepalive_connections=64))

    # ---- public API -------------------------------------------------------
    def generate(self, model: str, messages: list[dict[str, str]], temperature: float = 0.7,
                 max_tokens: int = 128, top_p: float = 0.9, seed: int | None = None,
                 **kwargs: Any) -> GenerationResult:
        raise NotImplementedError

    def health(self) -> bool:
        try:
            self.client.get(self.base_url, timeout=3.0)
            return True
        except httpx.HTTPError:
            return False

    def info(self, model: str) -> dict[str, Any]:
        """Backend/model version info for reproducibility."""
        return {"backend": self.name, "base_url": self.base_url}

    def prepare(self, model: str) -> None:
        """Called once before a model is benchmarked (e.g. unload other models)."""

    def release(self, model: str) -> None:
        """Called after a model is benchmarked (e.g. unload it to free VRAM)."""

    def resource_info(self, model: str) -> dict[str, Any]:
        """Server-reported memory info, if any."""
        return {}

    def close(self) -> None:
        self.client.close()

    # ---- helpers ----------------------------------------------------------
    @staticmethod
    def _iter_sse(resp: httpx.Response) -> Iterator[dict[str, Any]]:
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue

    @staticmethod
    def _finalize(res: GenerationResult, t0: float, n_chunks: int) -> GenerationResult:
        res.latency_ms = (time.perf_counter() - t0) * 1000
        res.text = strip_thinking(res.raw_text)
        if res.output_tokens is None:
            res.output_tokens = n_chunks
            res.token_count_source = "chunks"
        if res.decode_ms is None and res.ttft_any_ms is not None:
            res.decode_ms = res.latency_ms - res.ttft_any_ms
        if res.tokens_per_second is None and res.decode_ms and res.output_tokens and res.output_tokens > 1:
            # first token belongs to prefill; rate is over the remaining ones
            res.tokens_per_second = (res.output_tokens - 1) / (res.decode_ms / 1000)
        return res
