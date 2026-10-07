"""Streamlit UI:  streamlit run app.py"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import yaml

from evaluators.metrics import aggregate, category_table, rankings
from scenario import load_tests

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="NPC LLM Bench", layout="wide")

cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
models_cfg = {k: {**v, "id": k} for k, v in (yaml.safe_load((ROOT / "models.yaml").read_text(encoding="utf-8"))["models"]).items()}
RESULTS = ROOT / cfg.get("output", {}).get("results_dir", "results")
tests_all = load_tests(ROOT / cfg.get("scenario_dir", "scenarios"), "all")
scenario_files = sorted({t["scenario_file"] for t in tests_all})


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


# ------------------------------------------------------------------ sidebar: launch
st.sidebar.header("ベンチマーク実行")
enabled = [k for k, v in models_cfg.items() if v.get("enabled", True)]
sel_models = st.sidebar.multiselect("モデル", list(models_cfg), default=enabled[:3],
                                    format_func=lambda k: models_cfg[k].get("display_name", k))
sel_scen = st.sidebar.multiselect("シナリオ", scenario_files, default=scenario_files)
cand_tests = [t["id"] for t in tests_all if t["scenario_file"] in sel_scen]
sel_tests = st.sidebar.multiselect("テスト（空=選択シナリオ全部）", cand_tests)
mode = st.sidebar.selectbox("モード", ["quality", "perf", "both"], index=0)
runs = st.sidebar.number_input("品質テスト繰り返し回数", 1, 100, cfg.get("quality", {}).get("runs", 1))
conc = st.sidebar.text_input("同時実行数 (perf)", "1,2,4,8,16")
perf_runs = st.sidebar.number_input("perf 計測回数 / レベル", 5, 1000, cfg.get("perf", {}).get("runs", 30))
use_judge = st.sidebar.checkbox(f"LLM Judge ({cfg.get('judge', {}).get('model')})", value=cfg.get("judge", {}).get("enabled", False))

if st.sidebar.button("▶ benchmark開始", type="primary", disabled=not sel_models):
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-ui"
    cmd = [sys.executable, str(ROOT / "benchmark.py"), "--models", ",".join(sel_models), "--mode", mode,
           "--runs", str(runs), "--perf-runs", str(perf_runs), "--run-id", run_id, "--quiet",
           "--judge" if use_judge else "--no-judge"]
    if mode != "perf":
        cmd += ["--scenario", ",".join(sel_scen)]
        if sel_tests:
            cmd += ["--tests", ",".join(sel_tests)]
    if mode != "quality":
        cmd += ["--concurrency", conc]
    (RESULTS / run_id).mkdir(parents=True, exist_ok=True)
    subprocess.Popen(cmd, cwd=ROOT, stdout=open(RESULTS / run_id / "console.log", "w"), stderr=subprocess.STDOUT,
                     start_new_session=True)
    st.session_state["active_run"] = run_id
    st.session_state["view_run"] = run_id


@st.fragment(run_every=2)
def progress_panel() -> None:
    run_id = st.session_state.get("active_run")
    if not run_id:
        return
    p = RESULTS / run_id / "progress.json"
    if not p.exists():
        st.info(f"{run_id}: 起動中…")
        return
    pr = json.loads(p.read_text())
    total = max(pr.get("total") or 1, 1)
    st.progress(min(pr["done"] / total, 1.0),
                text=f"{run_id} · {pr.get('status')} · {pr.get('model')} · {pr.get('phase')} · {pr['done']}/{total} · {pr.get('current', '')}")
    with st.expander("ログ"):
        st.code("\n".join(pr.get("log", [])[-20:]))
    if pr.get("status") == "done":
        st.success("完了しました。下の結果ビューで確認できます。")
        st.session_state["active_run"] = None


st.title("NPC LLM Bench")
st.caption("日本語ゲームNPC用途のローカルLLM比較 — 会話品質 × 応答速度 × リソース")
progress_panel()

# ------------------------------------------------------------------ results
runs_avail = sorted([d.name for d in RESULTS.iterdir() if (d / "rows.jsonl").exists()], reverse=True) if RESULTS.exists() else []
if not runs_avail:
    st.info("結果がまだありません。左のサイドバーから実行してください。")
    st.stop()
default_idx = runs_avail.index(st.session_state["view_run"]) if st.session_state.get("view_run") in runs_avail else 0
run = st.selectbox("結果 (run)", runs_avail, index=default_idx)
run_dir = RESULTS / run
rows = read_jsonl(run_dir / "rows.jsonl")
perf = read_jsonl(run_dir / "perf.jsonl")
res = json.loads((run_dir / "resources.json").read_text()) if (run_dir / "resources.json").exists() else {}
summary = aggregate(rows, perf, res, models_cfg, cfg)
if summary.empty:
    st.warning("このrunにはまだ集計できる行がありません。")
    st.stop()
names = dict(zip(summary["model"], summary["display_name"]))

tab_cmp, tab_sum, tab_charts, tab_rank, tab_conc, tab_raw = st.tabs(
    ["返答の横並び比較", "サマリー", "グラフ", "ランキング", "同時実行", "全試行"])

with tab_cmp:
    q = [r for r in rows if r.get("phase") == "quality" and r.get("is_probe", True)]
    test_ids = list(dict.fromkeys(r["test_id"] for r in q))
    c1, c2 = st.columns([3, 1])
    tid = c1.selectbox("テスト", test_ids, format_func=lambda t: f"{t}  [{next(r['category'] for r in q if r['test_id'] == t)}]")
    max_run = max((r.get("run_index", 0) for r in q), default=0)
    ri = c2.number_input("run_index", 0, max_run, 0)
    sel = [r for r in q if r["test_id"] == tid and r.get("run_index", 0) == ri]
    if sel:
        msgs = next((r["messages"] for r in sel if r.get("messages")), None)
        st.markdown(f"**期待される振る舞い:** {sel[0].get('expected_behavior') or '—'}")
        if msgs:
            with st.expander("NPC設定（system prompt）"):
                st.text(msgs[0]["content"])
            for m in msgs[1:][-8:]:
                st.markdown(f"{'🧑 **プレイヤー**' if m['role'] == 'user' else '🎭 **NPC**'}: {m['content']}")
        cols = st.columns(len(sel))
        for col, r in zip(cols, sel):
            with col:
                st.markdown(f"#### {names.get(r['model'], r['model'])}")
                st.info(r.get("response") or f"ERROR: {r.get('error')}")
                st.caption(f"TTFT {r.get('ttft_ms')} ms · latency {r.get('latency_ms')} ms · {r.get('output_tokens')} tok · "
                           f"{r.get('tokens_per_second') and round(r['tokens_per_second'], 1)} tok/s")
                st.write(f"rule score: **{r.get('rule_total')}**")
                if r.get("rule_failed"):
                    st.error(", ".join(r["rule_failed"]))
                j = r.get("judge_score")
                if isinstance(j, dict) and "error" not in j:
                    st.write({k: v for k, v in j.items() if k != "comment"})
                    st.caption(j.get("comment", ""))

with tab_sum:
    show = summary.copy()
    for c in [c for c in show if c.startswith("q_") or c.startswith("score_")]:
        show[c] = (show[c] * 100).round(1)
    cols = ["display_name", "params", "quantization", "backend", "q_japanese", "q_character", "q_knowledge",
            "q_hallucination_rate", "q_long_conversation", "q_distinctiveness", "q_quality", "ttft_ms_p50", "ttft_ms_p90",
            "ttft_ms_p95", "ttft_ms_p99", "latency_ms_p50", "latency_ms_p95", "tok_s", "rps_max", "vram_peak_gb",
            "ram_peak_gb", "npc_score"]
    st.dataframe(show[[c for c in cols if c in show]].sort_values("npc_score", ascending=False), hide_index=True,
                 width="stretch")
    st.caption("品質系(q_*)は0-100、hallucination_rateは%。")
    cat = category_table(rows)
    if not cat.empty:
        st.subheader("カテゴリ別ルールスコア")
        cat["model"] = cat["model"].map(names)
        st.dataframe(cat.pivot(index="model", columns="category", values="rule_total").round(3), width="stretch")


def hbar(df: pd.DataFrame, x: str, title: str, fmt: str = ".1f", color: str = "#2a78d6", asc: bool = True):
    d = df.dropna(subset=[x])
    if d.empty:
        st.caption(f"{title}: データなし")
        return
    sort = "x" if asc else "-x"
    base = alt.Chart(d, title=title).encode(y=alt.Y("display_name:N", sort=sort, title=None),
                                            x=alt.X(f"{x}:Q", title=None),
                                            tooltip=["display_name", alt.Tooltip(f"{x}:Q", format=fmt)])
    st.altair_chart(base.mark_bar(color=color, cornerRadiusEnd=4, height=18)
                    + base.mark_text(align="left", dx=4, color="#52514e").encode(text=alt.Text(f"{x}:Q", format=fmt)),
                    width="stretch")


with tab_charts:
    s = summary.copy()
    s["quality100"] = s["q_quality"] * 100
    s["halluc_pct"] = s["q_hallucination_rate"] * 100
    st.subheader("Quality vs Latency（左上ほどNPC向き）")
    d = s.dropna(subset=["ttft_ms_p50", "quality100"])
    if not d.empty:
        pts = alt.Chart(d).encode(
            x=alt.X("ttft_ms_p50:Q", title="TTFT p50 (ms)  ← 速い", scale=alt.Scale(zero=True)),
            y=alt.Y("quality100:Q", title="NPC Quality (0-100)  ↑ 高品質", scale=alt.Scale(zero=False)),
            tooltip=["display_name", alt.Tooltip("ttft_ms_p50:Q", format=".0f"), alt.Tooltip("quality100:Q", format=".1f"),
                     alt.Tooltip("npc_score:Q", format=".1f")])
        st.altair_chart((pts.mark_circle(size=140, color="#2a78d6", stroke="white", strokeWidth=2)
                         + pts.mark_text(align="left", dx=9, dy=-7, color="#0b0b0b").encode(text="display_name")).properties(height=420),
                        width="stretch")
    a, b = st.columns(2)
    with a:
        hbar(s, "ttft_ms_p50", "TTFT p50 (ms) — 短いほど良い", ".0f")
        hbar(s, "vram_peak_gb", "VRAM peak (GB)", ".1f")
        hbar(s, "halluc_pct", "Hallucination rate (%)", ".1f", color="#e34948")
    with b:
        hbar(s, "tok_s", "tokens/sec — 大きいほど良い", ".1f", asc=False)
        hbar(s, "quality100", "品質スコア (0-100)", ".1f", asc=False)
        hbar(s, "npc_score", "Overall NPC score", ".1f", asc=False)
    comp = s.melt(id_vars=["display_name"], value_vars=[c for c in ["q_japanese", "q_character", "q_knowledge", "q_emotion",
                                                                    "q_long_conversation", "q_distinctiveness"] if c in s],
                  var_name="component", value_name="score").dropna()
    if not comp.empty:
        comp["score"] *= 100
        st.subheader("品質スコアの内訳")
        st.altair_chart(alt.Chart(comp).mark_bar(cornerRadiusEnd=3).encode(
            x=alt.X("score:Q", title=None, scale=alt.Scale(domain=[0, 100])), y=alt.Y("display_name:N", title=None),
            color=alt.Color("display_name:N", legend=None, scale=alt.Scale(range=["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                                                                                     "#e87ba4", "#008300", "#4a3aa7", "#e34948"])),
            row=alt.Row("component:N", title=None), tooltip=["display_name", "component", alt.Tooltip("score:Q", format=".1f")]
        ).properties(height=22 * len(s)), width="stretch")

with tab_rank:
    rk = rankings(summary)
    cols = st.columns(3)
    for i, (title, lst) in enumerate(rk.items()):
        with cols[i % 3]:
            st.markdown(f"**{title}**")
            st.markdown("\n".join(f"{x['rank']}. {x['model']} `({x['value']})`" for x in lst) or "—")

with tab_conc:
    if perf:
        pdf = pd.DataFrame(perf)
        pdf["display_name"] = pdf["model"].map(names).fillna(pdf["model"])
        a, b = st.columns(2)
        for col, y, t in ((a, "requests_per_sec", "requests/sec"), (b, "ttft_ms_p95", "TTFT p95 (ms)")):
            with col:
                st.altair_chart(alt.Chart(pdf, title=t).mark_line(point=True, strokeWidth=2).encode(
                    x=alt.X("concurrency:Q", scale=alt.Scale(type="log", base=2), title="同時リクエスト数"),
                    y=alt.Y(f"{y}:Q", title=None), color=alt.Color("display_name:N", title=None),
                    tooltip=["display_name", "concurrency", alt.Tooltip(f"{y}:Q", format=".1f")]), width="stretch")
        st.dataframe(pdf.drop(columns=["model"]).round(1), hide_index=True, width="stretch")
    else:
        st.caption("このrunには同時実行ベンチマークがありません（--concurrency 1,2,4,8,16 で実行）。")

with tab_raw:
    df = pd.DataFrame([{k: v for k, v in r.items() if k not in ("messages", "rule_score", "server_extra", "sampling")} for r in rows])
    st.dataframe(df, hide_index=True, width="stretch")
    meta_p = run_dir / "run_meta.json"
    if meta_p.exists():
        with st.expander("再現性メタデータ (run_meta.json)"):
            st.json(json.loads(meta_p.read_text()))
