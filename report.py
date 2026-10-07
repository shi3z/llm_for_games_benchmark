"""Report generation: summary CSV/JSON, rankings, charts (PNG) and a self-contained HTML report."""
from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from evaluators.metrics import aggregate, category_table, rankings  # noqa: E402

# Reference categorical palette (fixed order), text/grid tokens.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
TEXT, TEXT2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"

for fam in ("Noto Sans CJK JP", "IPAexGothic", "IPAGothic", "TakaoGothic", "Droid Sans Fallback"):
    if any(f.name == fam for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = fam
        break
plt.rcParams.update({
    "axes.edgecolor": GRID, "axes.labelcolor": TEXT2, "xtick.color": TEXT2, "ytick.color": TEXT2,
    "axes.titlecolor": TEXT, "axes.titlesize": 12, "axes.titleweight": "bold", "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE, "font.size": 10,
})


def _read_jsonl(p: Path) -> list[dict[str, Any]]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def load_run(run_dir: Path) -> tuple[list[dict], list[dict], dict, dict]:
    rows = _read_jsonl(run_dir / "rows.jsonl")
    perf = _read_jsonl(run_dir / "perf.jsonl")
    res = json.loads((run_dir / "resources.json").read_text()) if (run_dir / "resources.json").exists() else {}
    meta = json.loads((run_dir / "run_meta.json").read_text()) if (run_dir / "run_meta.json").exists() else {}
    return rows, perf, res, meta


# ------------------------------------------------------------------ charts
def _hbar(ax, labels, values, fmt, xlabel, color=SERIES[0]):
    y = range(len(labels))
    ax.barh(list(y), values, color=color, height=0.55, edgecolor=SURFACE, linewidth=2)
    ax.set_yticks(list(y), labels)
    ax.invert_yaxis()
    ax.set_xlabel(xlabel)
    ax.grid(axis="y", visible=False)
    vmax = max([v for v in values if v == v] or [1])
    for i, v in enumerate(values):
        if v == v:
            ax.text(v + vmax * 0.01, i, fmt(v), va="center", fontsize=9, color=TEXT)
    ax.set_xlim(0, vmax * 1.18 if vmax > 0 else 1)


def _fig(n: int, w: float = 8.0):
    return plt.subplots(figsize=(w, 1.2 + 0.5 * max(n, 2)))


def make_charts(s: pd.DataFrame, perf: pd.DataFrame, out: Path, cfg: dict[str, Any]) -> dict[str, Path]:
    charts: dict[str, Path] = {}
    if s.empty:
        return charts

    def save(fig, name):
        fig.tight_layout()
        p = out / f"{name}.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        charts[name] = p

    # 1. TTFT p50 bar + p95 marker
    d = s.dropna(subset=["ttft_ms_p50"]).sort_values("ttft_ms_p50")
    if not d.empty:
        fig, ax = _fig(len(d))
        y = list(range(len(d)))
        ax.barh(y, d["ttft_ms_p50"], color=SERIES[0], height=0.55, edgecolor=SURFACE, linewidth=2, label="p50")
        ax.scatter(d["ttft_ms_p95"], y, marker="|", s=260, linewidths=2.5, color=TEXT, label="p95", zorder=3)
        xmax = max(d["ttft_ms_p95"].max(), d["ttft_ms_p50"].max())
        for i, (p50, p95) in enumerate(zip(d["ttft_ms_p50"], d["ttft_ms_p95"])):
            ax.text(max(p50, p95 if p95 == p95 else 0) + xmax * 0.02, i, f"p50 {p50:.0f} / p95 {p95:.0f} ms",
                    va="center", fontsize=8.5, color=TEXT)
        ax.set_yticks(y, d["display_name"])
        ax.invert_yaxis()
        ax.grid(axis="y", visible=False)
        ax.set_xlim(0, xmax * 1.45)
        ax.set_xlabel("TTFT (ms) — 短いほど良い")
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, frameon=False, fontsize=9)
        ax.set_title("TTFT comparison (Time To First Token)", pad=22)
        save(fig, "ttft")

    # 2. tokens/sec
    d = s.dropna(subset=["tok_s"]).sort_values("tok_s", ascending=False)
    if not d.empty:
        fig, ax = _fig(len(d))
        _hbar(ax, d["display_name"], d["tok_s"], lambda v: f"{v:.1f}", "decode tokens/sec（1リクエスト、中央値）— 大きいほど良い")
        ax.set_title("tokens/sec comparison")
        save(fig, "tokens_per_sec")

    # 3. VRAM
    d = s.dropna(subset=["vram_peak_gb"]).sort_values("vram_peak_gb")
    if not d.empty:
        fig, ax = _fig(len(d))
        _hbar(ax, d["display_name"], d["vram_peak_gb"], lambda v: f"{v:.1f} GB", "VRAM peak (GB) — 小さいほど良い")
        ax.set_title("VRAM comparison")
        save(fig, "vram")

    # 4. quality vs latency: ideal = top-left (low TTFT, high quality)
    d = s.dropna(subset=["ttft_ms_p50", "q_quality"])
    if not d.empty:
        d = d.sort_values("q_quality", ascending=False)
        fig, ax = plt.subplots(figsize=(11, 5.8))
        x, y = d["ttft_ms_p50"], d["q_quality"] * 100
        xmax = max(x.max() * 1.15, 1)
        ylo = max(0, y.min() - 8)
        # ideal corner: fast (left) and high quality (top)
        ax.add_patch(plt.Rectangle((0, ylo + (100 - ylo) * 0.6), xmax * 0.35, (100 - ylo) * 0.4,
                                   color=SERIES[2], alpha=0.08, lw=0, zorder=0))
        ax.text(xmax * 0.01, 99.5, "◤ 理想：速い × 高品質", color=TEXT2, fontsize=9, va="top")
        ax.scatter(x, y, s=90, color=SERIES[0], edgecolor=SURFACE, linewidth=2, zorder=3)
        # numbered points + key on the right (names collide when models cluster)
        for i, (xi, yi) in enumerate(zip(x, y), 1):
            ax.annotate(str(i), (xi, yi), xytext=(6, 4), textcoords="offset points", fontsize=9, color=TEXT,
                        fontweight="bold")
        key = "\n".join(f"{i}. {n}  ({q:.1f}, {t:.0f}ms)" for i, (n, q, t) in enumerate(zip(d["display_name"], y, x), 1))
        ax.text(1.02, 1.0, key, transform=ax.transAxes, va="top", fontsize=8.5, color=TEXT, linespacing=1.6)
        ax.set_xlim(0, xmax)
        ax.set_ylim(ylo, 100)
        ax.set_xlabel("TTFT p50 (ms)  ← 速い　　遅い →")
        ax.set_ylabel("NPC Quality Score (0-100)  ↑ 高品質")
        ax.set_title("Quality vs Latency — 左上ほどNPC向き")
        save(fig, "quality_vs_latency")

    # 5. hallucination rate
    d = s.dropna(subset=["q_hallucination_rate"]).sort_values("q_hallucination_rate")
    if not d.empty:
        fig, ax = _fig(len(d))
        _hbar(ax, d["display_name"], d["q_hallucination_rate"] * 100, lambda v: f"{v:.1f}%",
              "Hallucination rate (%) — 知らない事実・嘘の前提を受け入れた割合（小さいほど良い）", color=SERIES[7])
        ax.set_title("Hallucination rate")
        save(fig, "hallucination")

    # 6. overall NPC score: stacked weighted contributions
    d = s.dropna(subset=["npc_score"]).sort_values("npc_score", ascending=False)
    if not d.empty:
        w = cfg.get("npc_score_weights", {})
        parts = [k for k in ("quality", "latency", "throughput", "memory_efficiency") if k in w]
        fig, ax = _fig(len(d), 9)
        left = [0.0] * len(d)
        for i, k in enumerate(parts):
            den = d.apply(lambda r: sum(w[p] for p in parts if pd.notna(r.get(f"score_{p}"))), axis=1)
            vals = (d[f"score_{k}"].fillna(0) * w[k] / den * 100).tolist()
            ax.barh(range(len(d)), vals, left=left, color=SERIES[i], height=0.55, edgecolor=SURFACE, linewidth=2,
                    label=f"{k} ×{w[k]}")
            left = [a + b for a, b in zip(left, vals)]
        for i, v in enumerate(d["npc_score"]):
            ax.text(v + 1, i, f"{v:.1f}", va="center", fontsize=9, color=TEXT)
        ax.set_yticks(range(len(d)), d["display_name"])
        ax.invert_yaxis()
        ax.set_xlim(0, 110)
        ax.grid(axis="y", visible=False)
        ax.set_xlabel("NPC Score (0-100) — 重み付き寄与の内訳")
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, frameon=False, fontsize=8.5)
        ax.set_title("Overall NPC score", pad=40)
        save(fig, "npc_score")

    # 7. concurrency (two panels, never dual axis)
    if not perf.empty and perf["concurrency"].nunique() > 1:
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
        names = dict(zip(s["model"], s["display_name"]))
        for i, (mid, g) in enumerate(perf.groupby("model", sort=False)):
            g = g.sort_values("concurrency")
            c = SERIES[i % len(SERIES)]
            axes[0].plot(g["concurrency"], g["requests_per_sec"], marker="o", ms=6, lw=2, color=c, label=names.get(mid, mid))
            axes[1].plot(g["concurrency"], g["ttft_ms_p95"], marker="o", ms=6, lw=2, color=c, label=names.get(mid, mid))
        for ax, t, yl in ((axes[0], "Throughput vs concurrency", "requests/sec"),
                          (axes[1], "TTFT p95 vs concurrency", "TTFT p95 (ms)")):
            ax.set_xscale("log", base=2)
            ax.set_xticks(sorted(perf["concurrency"].unique()), [str(v) for v in sorted(perf["concurrency"].unique())])
            ax.set_xlabel("同時リクエスト数")
            ax.set_ylabel(yl)
            ax.set_title(t)
        axes[0].legend(frameon=False, fontsize=8.5)
        save(fig, "concurrency")
    return charts


# ------------------------------------------------------------------ html
CSS = """
:root{--bg:#fcfcfb;--fg:#0b0b0b;--fg2:#52514e;--line:#e4e3df;--accent:#2a78d6;--bad:#c93a39;--card:#ffffff}
body{font-family:"Noto Sans CJK JP","Hiragino Sans",system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:24px 20px;line-height:1.55}
main{max-width:1200px;margin:0 auto}
h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.15rem;margin:32px 0 10px;border-bottom:1px solid var(--line);padding-bottom:4px}
.meta{color:var(--fg2);font-size:.85rem}
.wrap{overflow-x:auto}
table{border-collapse:collapse;font-size:.82rem;width:100%}
th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
th{background:#f3f2ee;position:sticky;top:0}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px}
.grid img{width:100%;border:1px solid var(--line);border-radius:6px;background:#fff}
.rank{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.rank div{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px 12px}
.rank b{display:block;font-size:.85rem;color:var(--fg2)}
.flag{color:var(--bad);font-size:.75rem}
details{margin:6px 0}summary{cursor:pointer;font-weight:600}
.resp{white-space:pre-wrap;min-width:180px}
"""


def _fmt(v: Any, nd: int = 1) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "–"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return html.escape(str(v))


SUMMARY_COLS = [
    ("display_name", "Model", None), ("params", "Params", None), ("quantization", "Quant", None), ("backend", "Backend", None),
    ("q_japanese", "日本語自然さ", 100), ("q_character", "キャラ一貫性", 100), ("q_knowledge", "知識整合性", 100),
    ("q_hallucination_rate", "Halluc.%", 100), ("q_long_conversation", "長期会話", 100), ("q_distinctiveness", "キャラ差異", 100),
    ("q_quality", "Overall dialogue", 100), ("ttft_ms_p50", "TTFT p50", 1), ("ttft_ms_p90", "p90", 1), ("ttft_ms_p95", "p95", 1),
    ("ttft_ms_p99", "p99", 1), ("latency_ms_p50", "Lat p50", 1), ("latency_ms_p95", "Lat p95", 1), ("tok_s", "tok/s", 1),
    ("rps_max", "req/s max", 1), ("vram_peak_gb", "VRAM GB", 1), ("ram_peak_gb", "RAM GB", 1), ("npc_score", "NPC Score", 1),
]


def summary_html(s: pd.DataFrame) -> str:
    head = "".join(f"<th>{h}</th>" for _, h, _ in SUMMARY_COLS)
    body = []
    for _, r in s.sort_values("npc_score", ascending=False, na_position="last").iterrows():
        cells = []
        for col, _, scale in SUMMARY_COLS:
            v = r.get(col)
            if scale and isinstance(v, (int, float)) and v == v:
                cells.append(f"<td class=num>{v * scale:.1f}</td>" if scale == 100 else f"<td class=num>{v:.1f}</td>")
            else:
                cells.append(f"<td>{_fmt(v)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<div class=wrap><table><tr>{head}</tr>{''.join(body)}</table></div>"


def comparison_html(rows: list[dict[str, Any]], names: dict[str, str], limit_runs: int = 1) -> str:
    """Side-by-side responses of every model to the same conversation."""
    q = [r for r in rows if r.get("phase") == "quality" and r.get("run_index", 0) < limit_runs and r.get("is_probe", True)]
    models = list(dict.fromkeys(r["model"] for r in q))
    by_test: dict[str, dict[str, dict]] = {}
    for r in q:
        by_test.setdefault(r["test_id"], {})[r["model"]] = r
    out = []
    for tid, per in by_test.items():
        any_r = next(iter(per.values()))
        msgs = any_r.get("messages") or []
        convo = "".join(f"<div><b>{'P' if m['role'] == 'user' else 'NPC'}:</b> {html.escape(m['content'])}</div>"
                        for m in msgs[1:][-6:])
        head = "".join(f"<th>{html.escape(names.get(m, m))}</th>" for m in models)
        cells = []
        for m in models:
            r = per.get(m)
            if not r:
                cells.append("<td>–</td>")
                continue
            j = r.get("judge_score") or {}
            jtxt = (" / judge " + "·".join(str(j[k]) for k in j if k not in ("comment", "error"))) if j and "error" not in j else ""
            flags = ", ".join(r.get("rule_failed") or [])
            cells.append(f"<td class=resp>{html.escape(r.get('response') or r.get('error') or '')}"
                         f"<div class=meta>TTFT {_fmt(r.get('ttft_ms'), 0)}ms · rule {_fmt(r.get('rule_total'), 2)}{jtxt}</div>"
                         + (f"<div class=flag>{html.escape(flags)}</div>" if flags else "") + "</td>")
        out.append(f"<details><summary>{html.escape(tid)} <span class=meta>[{html.escape(any_r.get('category', ''))}] "
                   f"{html.escape(any_r.get('expected_behavior') or '')}</span></summary>"
                   f"<div class=meta style='margin:6px 0'>{convo}</div>"
                   f"<div class=wrap><table><tr>{head}</tr><tr>{''.join(cells)}</tr></table></div></details>")
    return "\n".join(out)


def build_report(run_dir: Path, cfg: dict[str, Any], models: dict[str, dict[str, Any]]) -> Path:
    rows, perf, res, meta = load_run(run_dir)
    out = Path(cfg.get("output", {}).get("reports_dir", "reports"))
    if not out.is_absolute():
        out = Path(__file__).resolve().parent / out
    out = out / run_dir.name
    out.mkdir(parents=True, exist_ok=True)
    mcfg = {**{k: v for k, v in (meta.get("models") or {}).items()}, **models}
    s = aggregate(rows, perf, res, mcfg, cfg)
    ranks = rankings(s)
    perf_df = pd.DataFrame(perf)
    if not s.empty:
        s.to_csv(out / "summary.csv", index=False)
        s.to_json(out / "summary.json", orient="records", force_ascii=False, indent=2)
    if not perf_df.empty:
        perf_df.to_csv(out / "concurrency.csv", index=False)
    cat = category_table(rows)
    if not cat.empty:
        cat.to_csv(out / "by_category.csv", index=False)
    if rows:
        flat = pd.DataFrame([{k: v for k, v in r.items() if k not in ("messages", "rule_score", "server_extra")}
                             | {f"judge_{k}": v for k, v in (r.get("judge_score") or {}).items()} for r in rows])
        flat.to_csv(out / "trials.csv", index=False)
    (out / "rankings.json").write_text(json.dumps(ranks, ensure_ascii=False, indent=2))
    md = ["# Rankings", ""]
    for title, lst in ranks.items():
        md.append(f"## {title}")
        md += [f"{x['rank']}. {x['model']} — {x['value']}" for x in lst] or ["(no data)"]
        md.append("")
    (out / "rankings.md").write_text("\n".join(md), encoding="utf-8")

    charts = make_charts(s, perf_df, out, cfg)
    imgs = "".join(f"<img alt='{k}' src='data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}'>"
                   for k, p in charts.items())
    rank_html = "".join(
        f"<div><b>{html.escape(t)}</b>" + ("<br>".join(f"{x['rank']}. {html.escape(x['model'])} "
                                                     f"<span class=meta>({x['value']})</span>" for x in lst[:3]) or "–") + "</div>"
        for t, lst in ranks.items())
    names = dict(zip(s["model"], s["display_name"])) if not s.empty else {}
    sysinfo = meta.get("system", {})
    gpus = ", ".join(g["name"] for g in sysinfo.get("gpus", []))
    doc = f"""<!doctype html><html lang=ja><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>NPC LLM Bench {html.escape(run_dir.name)}</title><style>{CSS}</style></head><body><main>
<h1>NPC LLM Bench — {html.escape(run_dir.name)}</h1>
<div class=meta>{html.escape(meta.get('timestamp', ''))} · GPU {html.escape(gpus)} · driver {html.escape(str(sysinfo.get('driver_version')))} ·
CUDA {html.escape(str(sysinfo.get('cuda_driver_api')))} · sampling {html.escape(json.dumps(meta.get('sampling', {})))} · seed {meta.get('seed')} ·
judge {html.escape(str((meta.get('judge') or {}).get('model', 'off')))}</div>
<h2>Rankings</h2><div class=rank>{rank_html}</div>
<h2>Summary</h2>{summary_html(s) if not s.empty else '<p>no data</p>'}
<p class=meta>品質系の列は0-100。Halluc.%は「禁止事実の断定・嘘の前提への同意」の割合。速度は perf(同時実行1) の計測、無い場合は品質テスト時の計測。</p>
<h2>Charts</h2><div class=grid>{imgs}</div>
<h2>同じ会話への各モデルの返答（横並び比較）</h2>{comparison_html(rows, names)}
</main></body></html>"""
    (out / "report.html").write_text(doc, encoding="utf-8")
    return out
