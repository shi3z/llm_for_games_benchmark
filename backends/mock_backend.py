"""Offline mock backend for testing the pipeline without a GPU/server."""
from __future__ import annotations

import random
import time

from .base import Backend, GenerationResult

_LINES = [
    "……知らないね。そんなこと、あたしに聞かれても困る。",
    "さあな。悪いけど、他を当たってくれ。",
    "は？ 何の話だよ。あたしはここで働いてるだけ。",
    "一万円なら覚えてる。百万なんて借りてないだろ。",
    "痛っ……近寄らないで。今は話したくない。",
]


class MockBackend(Backend):
    name = "mock"

    def __init__(self, base_url: str = "mock://", *a, **kw):
        super().__init__("http://localhost", *a, **kw)
        self.speed = float(self.model_cfg.get("mock_tok_per_s", 80))
        self.ttft = float(self.model_cfg.get("mock_ttft_ms", 120))

    def generate(self, model, messages, temperature=0.7, max_tokens=128, top_p=0.9, seed=None, **kwargs):
        rng = random.Random(seed)
        t0 = time.perf_counter()
        time.sleep(self.ttft / 1000 * rng.uniform(0.8, 1.3))
        ttft = (time.perf_counter() - t0) * 1000
        text = rng.choice(_LINES)
        n = min(max_tokens, len(text))
        time.sleep(n / self.speed)
        res = GenerationResult(text=text, raw_text=text, ttft_ms=ttft, ttft_any_ms=ttft,
                               input_tokens=sum(len(m["content"]) for m in messages) // 2, output_tokens=n)
        return self._finalize(res, t0, n)

    def health(self):
        return True

    def info(self, model):
        return {"backend": "mock", "backend_version": "0"}
