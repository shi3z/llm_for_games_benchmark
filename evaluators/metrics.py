"""Aggregation: percentiles, quality components, NPC score and rankings."""
from __future__ import annotations

import itertools
import re
from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

PCTS = (50, 90, 95, 99)

NATURALNESS_FLAGS = ["language_mix", "excess_politeness", "ai_tone", "verbosity", "repetition", "stage_direction"]
CHARACTER_FLAGS = ["identity_failure", "ai_leak", "meta_leak", "wrong_pronoun"]


def percentiles(values: list[float] | pd.Series, prefix: str) -> dict[str, float | None]:
    v = np.asarray([x for x in values if x is not None and not pd.isna(x)], dtype=float)
    if v.size == 0:
        return {f"{prefix}_p{p}": None for p in PCTS} | {f"{prefix}_mean": None}
    out = {f"{prefix}_p{p}": float(np.percentile(v, p)) for p in PCTS}
    out[f"{prefix}_mean"] = float(v.mean())
    return out


def _lin(x: float | None, good: float, bad: float) -> float | None:
    """Map x to [0,1]; `good` -> 1, `bad` -> 0 (works for either direction)."""
    if x is None or pd.isna(x):
        return None
    return float(np.clip((x - bad) / (good - bad), 0.0, 1.0))


def _flag_rate(df: pd.DataFrame, flags: list[str]) -> float | None:
    if df.empty:
        return None
    hit = df["rule_score"].apply(lambda s: any((s or {}).get(f) for f in flags))
    return float(hit.mean())


def _judge_mean(df: pd.DataFrame, key: str) -> float | None:
    vals = [j.get(key) for j in df["judge_score"] if isinstance(j, dict) and key in j]
    return (float(np.mean(vals)) - 1) / 4 if vals else None


def _blend(rule: float | None, judge: float | None, judge_weight: float) -> float | None:
    if rule is None:
        return judge
    if judge is None:
        return rule
    return judge_weight * judge + (1 - judge_weight) * rule


PRONOUNS = re.compile(r"あたし|わたし|私|俺|オレ|おれ|僕|ボク|わし|自分|あんた|あなた|お前|おまえ|君|キミ|きみ|兄ちゃん|若いの|貴様")
ENDING_STRIP = re.compile(r"[。！？!?…、・「」『』\s〜ー]+$")


def style_features(text: str) -> set[str]:
    """Pronouns + sentence-final endings (last 2 chars) — the cues that make characters sound different."""
    feats = {f"p:{m}" for m in PRONOUNS.findall(text)}
    for sent in re.split(r"(?<=[。！？!?\n…])", text):
        sent = ENDING_STRIP.sub("", sent)
        if len(sent) >= 2:
            feats.add(f"e:{sent[-2:]}")
    return feats


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def distinctiveness(df: pd.DataFrame) -> float | None:
    """1 - mean pairwise style-feature similarity of answers by different characters to the same question."""
    g = df[df["group"].notna()]
    sims = []
    for _, grp in g.groupby(["group", "run_index"]):
        feats = [style_features(r) for r in grp["response"] if r]
        sims += [_jaccard(a, b) for a, b in itertools.combinations(feats, 2)]
    return float(1 - np.mean(sims)) if sims else None


def quality_components(q: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    jw = cfg.get("judge_blend", 0.6)
    know = q[q["category"].isin(["knowledge_boundary", "memory"]) | q["has_halluc_check"]]
    halluc_rows = q[q["has_halluc_check"]]
    char_rows = q[q["category"].isin(["personality", "injection", "character_diff", "long_dialogue"])]
    state_rows = q[q["category"] == "state"]
    long_rows = q[(q["category"] == "long_dialogue") & q["is_probe"]]

    c: dict[str, Any] = {}
    c["hallucination_rate"] = _flag_rate(halluc_rows, ["hallucination"])
    c["japanese"] = _blend(None if q.empty else 1 - _flag_rate(q, NATURALNESS_FLAGS), _judge_mean(q, "natural_japanese"), jw)
    cr = _flag_rate(char_rows, CHARACTER_FLAGS)
    c["character"] = _blend(None if cr is None else 1 - cr, _judge_mean(char_rows if not char_rows.empty else q, "character_consistency"), jw)
    kr = _flag_rate(know, ["hallucination", "missing_required"])
    c["knowledge"] = _blend(None if kr is None else 1 - kr, _judge_mean(know if not know.empty else q, "knowledge_consistency"), jw)
    sr = _flag_rate(state_rows, ["state_not_reflected"])
    c["emotion"] = _blend(None if sr is None else 1 - sr, _judge_mean(q, "emotional_consistency"), jw)
    lr = float(long_rows["rule_total"].mean()) if not long_rows.empty else None
    c["long_conversation"] = _blend(lr, _judge_mean(q[q["category"] == "long_dialogue"], "character_consistency"), jw)
    c["distinctiveness"] = distinctiveness(q)
    c["rule_total"] = float(q["rule_total"].mean()) if not q.empty else None
    c["judge_dialogue"] = _judge_mean(q, "game_dialogue_quality")

    weights = cfg.get("quality_weights", {})
    num = den = 0.0
    for k, w in weights.items():
        v = c.get(k)
        if v is not None:
            num += w * v
            den += w
    c["quality"] = num / den if den else None
    return c


def aggregate(rows: list[dict[str, Any]], perf: list[dict[str, Any]], resources: dict[str, Any],
              models: dict[str, dict[str, Any]], cfg: dict[str, Any]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame()
    for col, default in (("group", None), ("is_probe", True), ("has_halluc_check", False), ("judge_score", None)):
        if col not in df:
            df[col] = default
    df["is_probe"] = df["is_probe"].fillna(True).astype(bool)
    df["has_halluc_check"] = df["has_halluc_check"].fillna(False).astype(bool)
    perf_df = pd.DataFrame(perf)
    sc = cfg.get("scoring", {})
    out = []
    for mid, mdf in df.groupby("model", sort=False):
        m = models.get(mid, {})
        ok = mdf[mdf["error"].isna()]
        q = ok[ok["phase"] == "quality"]
        p1 = ok[(ok["phase"] == "perf") & (ok["concurrency"] == 1)]
        speed_src = p1 if not p1.empty else q
        rec: dict[str, Any] = {
            "model": mid, "display_name": m.get("display_name", mid), "params": m.get("params"),
            "quantization": m.get("quantization"), "backend": m.get("backend"),
            "n_quality": len(q), "n_perf": int((ok["phase"] == "perf").sum()),
            "errors": int(mdf["error"].notna().sum()), "speed_source": "perf_c1" if not p1.empty else "quality",
        }
        rec |= {f"q_{k}": v for k, v in quality_components(q, cfg).items()}
        rec |= percentiles(speed_src["ttft_ms"], "ttft_ms")
        rec |= percentiles(speed_src["latency_ms"], "latency_ms")
        rec["tok_s"] = float(speed_src["tokens_per_second"].median()) if speed_src["tokens_per_second"].notna().any() else None
        rec["input_tokens_mean"] = float(speed_src["input_tokens"].mean()) if speed_src["input_tokens"].notna().any() else None
        rec["output_tokens_mean"] = float(speed_src["output_tokens"].mean()) if speed_src["output_tokens"].notna().any() else None
        rec["prefill_ms_p50"] = float(speed_src["prefill_ms"].median()) if speed_src["prefill_ms"].notna().any() else None
        if not perf_df.empty and mid in set(perf_df["model"]):
            mp = perf_df[perf_df["model"] == mid]
            best = mp.loc[mp["requests_per_sec"].idxmax()]
            rec["rps_max"] = float(best["requests_per_sec"])
            rec["rps_max_concurrency"] = int(best["concurrency"])
            rec["agg_tok_s_max"] = float(mp["agg_tokens_per_sec"].max())
            c1 = mp[mp["concurrency"] == 1]
            rec["rps_c1"] = float(c1["requests_per_sec"].iloc[0]) if not c1.empty else None
        else:
            lat = rec.get("latency_ms_mean")
            rec["rps_c1"] = 1000 / lat if lat else None
            rec["rps_max"] = rec["rps_c1"]
            rec["agg_tok_s_max"] = rec["tok_s"]
        r = resources.get(mid, {})
        rec["vram_peak_gb"] = r.get("vram_peak_mb", 0) / 1024 if r.get("vram_peak_mb") else None
        rec["vram_method"] = r.get("vram_method")
        rec["ram_peak_gb"] = r.get("ram_peak_mb", 0) / 1024 if r.get("ram_peak_mb") else None
        rec["gpu_util_mean"] = r.get("gpu_util_mean")
        rec["gpu_util_max"] = r.get("gpu_util_max")

        # ---- NPC score
        s_lat_t = _lin(rec.get("ttft_ms_p95"), sc.get("ttft_good_ms", 150), sc.get("ttft_bad_ms", 1500))
        s_lat_l = _lin(rec.get("latency_ms_p95"), sc.get("latency_good_ms", 600), sc.get("latency_bad_ms", 4000))
        s_lat = None if s_lat_t is None else (s_lat_t if s_lat_l is None else 0.6 * s_lat_t + 0.4 * s_lat_l)
        s_tp_tok = _lin(rec.get("tok_s"), sc.get("tok_s_good", 100), sc.get("tok_s_bad", 10))
        s_tp_rps = _lin(rec.get("rps_max"), sc.get("rps_good", 8), sc.get("rps_bad", 0.3))
        tp_parts = [x for x in (s_tp_tok, s_tp_rps) if x is not None]
        s_tp = float(np.mean(tp_parts)) if tp_parts else None
        s_mem = _lin(rec.get("vram_peak_gb"), sc.get("vram_good_gb", 6), sc.get("vram_bad_gb", 24))
        parts = {"quality": rec.get("q_quality"), "latency": s_lat, "throughput": s_tp, "memory_efficiency": s_mem}
        rec |= {f"score_{k}": (float(v) if v is not None else None) for k, v in parts.items()}
        weights = cfg.get("npc_score_weights", {})
        num = den = 0.0
        for k, w in weights.items():
            if parts.get(k) is not None:
                num += w * parts[k]
                den += w
        rec["npc_score"] = 100 * num / den if den else None
        rec["npc_score_missing"] = ",".join(k for k in weights if parts.get(k) is None)
        out.append(rec)
    return pd.DataFrame(out)


RANKINGS = [
    ("Best Quality", "q_quality", False),
    ("Best Latency (TTFT p50)", "ttft_ms_p50", True),
    ("Best Japanese", "q_japanese", False),
    ("Best Character Consistency", "q_character", False),
    ("Lowest Hallucination", "q_hallucination_rate", True),
    ("Best VRAM Efficiency (quality / GB)", "quality_per_gb", False),
    ("Best Overall NPC Model", "npc_score", False),
]


def rankings(summary: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    s = summary.copy()
    if s.empty:
        return {}
    s["quality_per_gb"] = s["q_quality"] / s["vram_peak_gb"] if "vram_peak_gb" in s else None
    out = {}
    for title, col, asc in RANKINGS:
        if col not in s or s[col].isna().all():
            out[title] = []
            continue
        r = s[s[col].notna()].sort_values(col, ascending=asc)
        out[title] = [{"rank": i + 1, "model": row["display_name"], "value": round(float(row[col]), 4)}
                      for i, (_, row) in enumerate(r.iterrows())]
    return out


def perf_rollup(rows: list[dict[str, Any]], wall_s: float, model: str, concurrency: int) -> dict[str, Any]:
    ok = [r for r in rows if not r.get("error")]
    out_tok = sum(r.get("output_tokens") or 0 for r in ok)
    rec = {"model": model, "concurrency": concurrency, "n": len(rows), "errors": len(rows) - len(ok),
           "wall_s": wall_s, "requests_per_sec": len(ok) / wall_s if wall_s else None,
           "agg_tokens_per_sec": out_tok / wall_s if wall_s else None}
    rec |= percentiles([r.get("ttft_ms") for r in ok], "ttft_ms")
    rec |= percentiles([r.get("latency_ms") for r in ok], "latency_ms")
    tps = [r["tokens_per_second"] for r in ok if r.get("tokens_per_second")]
    rec["per_request_tok_s_p50"] = float(np.median(tps)) if tps else None
    return rec


def category_table(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Rule pass rate & judge mean per model x category."""
    df = pd.DataFrame([r for r in rows if r.get("phase") == "quality" and not r.get("error")])
    if df.empty:
        return df
    agg: dict[tuple, dict[str, Any]] = defaultdict(dict)
    for (m, c), g in df.groupby(["model", "category"]):
        agg[(m, c)]["rule_total"] = g["rule_total"].mean()
        js = [np.mean([j[k] for k in j if k != "comment" and isinstance(j[k], (int, float))])
              for j in g["judge_score"] if isinstance(j, dict) and "error" not in j]
        agg[(m, c)]["judge_mean"] = float(np.mean(js)) if js else None
    return pd.DataFrame([{"model": m, "category": c, **v} for (m, c), v in agg.items()])
