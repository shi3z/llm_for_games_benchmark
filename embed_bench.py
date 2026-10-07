"""Embedding-model benchmark for game NPCs (retrieval / detection / speed).

Generative NPC quality can't be measured on an embedding model, so this measures what an NPC
pipeline uses embeddings for: pulling the right memory/fact (RAG), flagging prompt-injection and
false-premise player lines, and how fast / how big it is.

  python embed_bench.py --model google/embeddinggemma-2 --dims 768,512,256,128 --out results/embedding_gemma2.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import numpy as np
import torch
import yaml

from scenario import load_characters, load_tests

ROOT = Path(__file__).parent

# prefixes by model family; unknown models run without prefixes
PREFIXES = {
    "embeddinggemma-2": dict(query="task: search result | query: {}", doc="title: none | text: {}",
                             clf="task: classification | query: {}"),
    "embeddinggemma-300m": dict(query="task: search result | query: {}", doc="title: none | text: {}",
                                clf="task: classification | query: {}"),
}


def prefixes_for(model_id: str) -> dict:
    for k, v in PREFIXES.items():
        if k in model_id:
            return v
    return dict(query="{}", doc="{}", clf="{}")


def build_corpus(chars: dict) -> list[dict]:
    docs = []
    for cid, ch in chars.items():
        for kind in ("known_facts", "unknown_facts", "memories", "relationships"):
            for text in ch.get(kind) or []:
                docs.append({"character": cid, "kind": kind, "text": text})
    return docs


def rank_metrics(ranks: list[int]) -> dict:
    n = len(ranks)
    return {"n": n, "mrr": sum(1 / r for r in ranks) / n,
            "r@1": sum(r <= 1 for r in ranks) / n, "r@3": sum(r <= 3 for r in ranks) / n}


def auc(scores_pos: list[float], scores_neg: list[float]) -> float:
    wins = sum((p > q) + 0.5 * (p == q) for p in scores_pos for q in scores_neg)
    return wins / (len(scores_pos) * len(scores_neg))


def truncate(x: np.ndarray, d: int) -> np.ndarray:
    x = x[:, :d]
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def loo_detection_auc(vecs: np.ndarray, labels: np.ndarray, k: int = 3) -> float:
    """Leave-one-out: score = mean top-k cosine to the *other* positive items minus to negatives."""
    sims = vecs @ vecs.T
    np.fill_diagonal(sims, -np.inf)
    scores = []
    for i in range(len(vecs)):
        pos = np.sort(sims[i][labels == 1])[::-1][:k]
        neg = np.sort(sims[i][labels == 0])[::-1][:k]
        scores.append(pos[np.isfinite(pos)].mean() - neg[np.isfinite(neg)].mean())
    s = np.array(scores)
    return auc(list(s[labels == 1]), list(s[labels == 0]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--dims", default="768,512,256,128")
    ap.add_argument("--out", required=True)
    ap.add_argument("--text-only", action="store_true", help="load without vision/audio encoders (EmbeddingGemma 2)")
    ap.add_argument("--speed-runs", type=int, default=100)
    ap.add_argument("--revision", default=None)
    args = ap.parse_args()

    from sentence_transformers import SentenceTransformer
    torch.manual_seed(0)
    dev = "cuda"
    kw = {}
    if args.text_only:
        kw["config_kwargs"] = {"vision_config": None, "audio_config": None}
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    model = SentenceTransformer(args.model, device=dev, model_kwargs={"torch_dtype": torch.bfloat16},
                                revision=args.revision, **kw)
    load_s = time.time() - t0
    weights_gb = torch.cuda.memory_allocated() / 1e9
    P = prefixes_for(args.model)
    full_dim = model.get_sentence_embedding_dimension()
    dims = [d for d in map(int, args.dims.split(",")) if d <= full_dim]

    def enc(texts):
        return model.encode(texts, batch_size=32, convert_to_numpy=True, normalize_embeddings=True,
                            show_progress_bar=False).astype(np.float32)

    chars = load_characters(ROOT / "scenarios/characters.yaml")
    corpus = build_corpus(chars)
    queries = yaml.safe_load(open(ROOT / "scenarios/embedding_retrieval.yaml", encoding="utf-8"))["queries"]
    for q in queries:  # resolve gold -> corpus index; must be unique within the character
        hits = [i for i, d in enumerate(corpus) if d["character"] == q["character"] and q["gold"] in d["text"]]
        assert len(hits) == 1, (q["id"], q["gold"], hits)
        q["gold_idx"] = hits[0]

    D = enc([P["doc"].format(d["text"]) for d in corpus])
    Q = enc([P["query"].format(q["user"]) for q in queries])

    tests = load_tests(ROOT / "scenarios")
    tests = [t for t in tests if isinstance(t.get("user"), str)]
    det_vecs = enc([P["clf"].format(t["user"]) for t in tests])
    inj = np.array([t["category"] == "injection" for t in tests], dtype=int)
    fp = np.array([bool((t.get("checks") or {}).get("false_premise")) for t in tests], dtype=int)

    char_idx = {c: [i for i, d in enumerate(corpus) if d["character"] == c] for c in chars}
    result = {"model": args.model, "revision": args.revision, "dtype": "bfloat16", "full_dim": full_dim,
              "text_only": args.text_only, "corpus_size": len(corpus), "n_queries": len(queries),
              "n_detection_items": len(tests), "n_injection": int(inj.sum()), "n_false_premise": int(fp.sum()),
              "load_s": round(load_s, 2), "weights_gb": round(weights_gb, 3), "by_dim": {}}

    for d in dims:
        Dd, Qd = truncate(D, d), truncate(Q, d)
        sims = Qd @ Dd.T
        g_ranks, l_ranks, unk_ranks = [], [], []
        for qi, q in enumerate(queries):
            order = np.argsort(-sims[qi])
            g_ranks.append(int(np.where(order == q["gold_idx"])[0][0]) + 1)
            own = char_idx[q["character"]]
            lo = sorted(own, key=lambda i: -sims[qi][i])
            l_ranks.append(lo.index(q["gold_idx"]) + 1)
            if corpus[q["gold_idx"]]["kind"] == "unknown_facts":
                unk_ranks.append(l_ranks[-1])
        # does the top global hit belong to the asking character?
        own_char = float(np.mean([corpus[int(np.argmax(sims[qi]))]["character"] == q["character"]
                                  for qi, q in enumerate(queries)]))
        Vd = truncate(det_vecs, d)
        result["by_dim"][d] = {
            "global": rank_metrics(g_ranks), "per_character": rank_metrics(l_ranks),
            "top1_own_character": own_char,
            "unknown_fact_queries": rank_metrics(unk_ranks) if unk_ranks else None,
            "injection_auc": loo_detection_auc(Vd, inj), "false_premise_auc": loo_detection_auc(Vd, fp),
            "bytes_per_vec_fp16": d * 2,
        }

    # ---- speed (full dim) ----
    short = [P["query"].format(q["user"]) for q in queries]
    long_doc = P["doc"].format("".join(d["text"] + "。" for d in corpus[:40]))
    for _ in range(10):
        model.encode(short[:1], convert_to_numpy=True)
    lat = {}
    for name, txt in (("query_1", short[:1]), ("doc_long_1", [long_doc])):
        ts = []
        for i in range(args.speed_runs):
            torch.cuda.synchronize(); a = time.perf_counter()
            model.encode(txt if name != "query_1" else [short[i % len(short)]], convert_to_numpy=True)
            torch.cuda.synchronize(); ts.append((time.perf_counter() - a) * 1000)
        ts.sort()
        lat[name] = {"p50_ms": round(statistics.median(ts), 2), "p95_ms": round(ts[int(len(ts) * .95) - 1], 2)}
    batch_texts = [P["doc"].format(d["text"]) for d in corpus] * 8
    tps = {}
    for bs in (1, 8, 32, 128):
        model.encode(batch_texts[:bs], batch_size=bs, convert_to_numpy=True)
        torch.cuda.synchronize(); a = time.perf_counter(); n = 0
        while time.perf_counter() - a < 3:
            model.encode(batch_texts, batch_size=bs, convert_to_numpy=True); n += len(batch_texts)
        torch.cuda.synchronize()
        tps[bs] = round(n / (time.perf_counter() - a), 1)
    result["latency"] = lat
    result["docs_per_s_by_batch"] = tps
    result["peak_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 3)
    result["gpu"] = torch.cuda.get_device_name(0)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
