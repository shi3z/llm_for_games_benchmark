#!/usr/bin/env python
"""NPC LLM benchmark: Japanese game-NPC dialogue quality + inference performance.

Examples:
  python benchmark.py --models qwen3-14b,elyza-8b --scenario all --runs 3
  python benchmark.py --models all --concurrency 1,2,4,8,16
  python benchmark.py --judge-only results/20261006-120000
  python benchmark.py --report-only results/20261006-120000
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import signal
import subprocess
import sys
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from backends import create_backend
from evaluators import rules
from evaluators.llm_judge import LLMJudge
from evaluators.metrics import perf_rollup
from monitor import ResourceMonitor, system_info
from scenario import build_messages, load_characters, load_tests, resolve_character, sha, to_messages

ROOT = Path(__file__).resolve().parent


# ------------------------------------------------------------------ config
def load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_models(path: str) -> dict[str, dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    models = {}
    for mid, m in (data.get("models") or {}).items():
        m = dict(m)
        m["id"] = mid
        m.setdefault("model", mid)
        m.setdefault("display_name", mid)
        models[mid] = m
    return models


def select_models(models: dict[str, dict[str, Any]], spec: str) -> list[dict[str, Any]]:
    if spec in ("all", ""):
        return [m for m in models.values() if m.get("enabled", True)]
    out = []
    for name in spec.split(","):
        name = name.strip()
        if name not in models:
            sys.exit(f"model '{name}' not in models.yaml (available: {', '.join(models)})")
        out.append(models[name])
    return out


# ------------------------------------------------------------------ io helpers
class RunWriter:
    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.progress = {"status": "running", "done": 0, "total": 0, "model": None, "phase": None,
                         "started_at": datetime.now().isoformat(timespec="seconds"), "log": []}

    def row(self, row: dict[str, Any], file: str = "rows.jsonl") -> None:
        with self.lock, open(self.dir / file, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def json(self, name: str, obj: Any) -> None:
        with open(self.dir / name, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, default=str)

    def tick(self, n: int = 1, **kw: Any) -> None:
        with self.lock:
            self.progress["done"] += n
            self.progress.update(kw)
            self._flush()

    def log(self, msg: str) -> None:
        print(msg, flush=True)
        with self.lock:
            self.progress["log"] = (self.progress["log"] + [msg])[-50:]
            self._flush()

    def _flush(self) -> None:
        tmp = self.dir / "progress.json.tmp"
        tmp.write_text(json.dumps(self.progress, ensure_ascii=False))
        tmp.replace(self.dir / "progress.json")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ------------------------------------------------------------------ server lifecycle
def unload_ollama(url: str) -> None:
    """Free VRAM held by an Ollama server (models are kept resident for keep_alive)."""
    import httpx
    try:
        for m in httpx.get(url + "/api/ps", timeout=3).json().get("models", []):
            httpx.post(url + "/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=30)
        time.sleep(1.5)
    except httpx.HTTPError:
        pass


class ManagedServer:
    """Optionally start/stop an inference server per model (models.yaml: launch)."""

    def __init__(self, mcfg: dict[str, Any], log_path: Path):
        self.cfg = mcfg.get("launch") or {}
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None

    def start(self, backend, writer: RunWriter) -> None:
        if not self.cfg.get("cmd"):
            return
        env = {**os.environ, **{k: str(v) for k, v in (self.cfg.get("env") or {}).items()}}
        writer.log(f"  launching server: {self.cfg['cmd']}")
        cmd = shlex.split(os.path.expandvars(self.cfg["cmd"]))
        self.proc = subprocess.Popen(cmd, env=env, cwd=self.cfg.get("cwd") or ROOT,
                                     stdout=open(self.log_path, "w"), stderr=subprocess.STDOUT,
                                     start_new_session=True)
        deadline = time.time() + self.cfg.get("ready_timeout", 600)
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited with {self.proc.returncode}; see {self.log_path}")
            if backend.health():
                writer.log("  server ready")
                return
            time.sleep(2)
        raise TimeoutError("server did not become ready")

    @property
    def pids(self) -> list[int]:
        return [self.proc.pid] if self.proc else []

    def stop(self) -> None:
        if not self.proc:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(timeout=60)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(self.cfg.get("cooldown", 3))


# ------------------------------------------------------------------ benchmark core
def base_row(run_id: str, m: dict[str, Any], sampling: dict[str, Any]) -> dict[str, Any]:
    return {"run_id": run_id, "model": m["id"], "model_name": m["model"], "display_name": m["display_name"],
            "backend": m.get("backend"), "params": m.get("params"), "quantization": m.get("quantization"),
            "sampling": sampling}


def result_fields(r) -> dict[str, Any]:
    return {"input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
            "ttft_ms": _r(r.ttft_ms), "ttft_any_ms": _r(r.ttft_any_ms), "latency_ms": _r(r.latency_ms),
            "tokens_per_second": _r(r.tokens_per_second), "prefill_ms": _r(r.prefill_ms), "decode_ms": _r(r.decode_ms),
            "finish_reason": r.finish_reason, "token_count_source": r.token_count_source, "error": r.error,
            "response": r.text, "raw_response": r.raw_text if r.raw_text.strip() != r.text else None,
            "server_extra": r.extra or None}


def _r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(x, nd)


def hf_revision(repo: str) -> str | None:
    """Snapshot hash of a HF repo in the local cache (vLLM / transformers models)."""
    if repo.count("/") != 1:
        return None
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub"
    ref = hub / f"models--{repo.replace('/', '--')}" / "refs" / "main"
    return ref.read_text().strip() if ref.exists() else None


def test_seed(base: int, test_id: str, run_index: int) -> int:
    return base + run_index * 1000 + zlib.crc32(test_id.encode()) % 997


class Bench:
    def __init__(self, cfg: dict[str, Any], args: argparse.Namespace, writer: RunWriter):
        self.cfg, self.args, self.w = cfg, args, writer
        s = cfg["sampling"]
        self.sampling = {"temperature": s["temperature"], "top_p": s["top_p"], "max_tokens": s["max_tokens"]}
        self.base_prompt = cfg["npc_system_prompt"]
        self.history_mode = cfg.get("history_mode", "messages")
        self.defaults = cfg.get("rules", {})
        self.characters = load_characters(ROOT / cfg.get("scenario_dir", "scenarios") / "characters.yaml")

    # ---- one generation + rule evaluation
    def _gen(self, backend, m, messages, seed, solo=True):
        return backend.generate(model=m["model"], messages=messages, seed=seed, solo=solo, **self.sampling)

    def _quality_row(self, m, test, ch, system, messages, history, res, run_index, seed, turn=None, checks=None,
                     is_probe=True) -> dict[str, Any]:
        t = {**test, "checks": checks if checks is not None else test.get("checks")}
        ev = rules.evaluate(res.text, t, ch, self.defaults, history=history,
                            weights=self.cfg.get("rule_weights"), finish_reason=res.finish_reason)
        ck = t["checks"] or {}
        row = base_row(self.w.dir.name, m, self.sampling) | {
            "phase": "quality", "test_id": test["id"] + (f"#t{turn:02d}" if turn is not None else ""),
            "base_test_id": test["id"], "category": test["category"], "character": test["character"],
            "group": test.get("group"), "turn": turn, "run_index": run_index, "seed": seed, "concurrency": 1,
            "is_probe": is_probe, "has_halluc_check": bool(ck.get("forbidden") or ck.get("false_premise")),
            "expected_behavior": test.get("expected_behavior"), "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            **result_fields(res),
            "rule_score": ev["flags"], "rule_failed": ev["failed"], "rule_total": ev["rule_total"],
            "rule_notes": ev["notes"], "judge_score": None,
            "prompt_hash": sha(system), "messages": messages,
        }
        return row

    def run_quality(self, backend, m, tests) -> None:
        runs = self.args.runs or self.cfg.get("quality", {}).get("runs", 1)
        for run_index in range(runs):
            for test in tests:
                ch = resolve_character(test, self.characters)
                seed = test_seed(self.cfg.get("seed", 1234), test["id"], run_index)
                if test.get("script"):
                    self._run_interactive(backend, m, test, ch, run_index, seed)
                    continue
                history = to_messages(test.get("history"))
                messages, system = build_messages(self.base_prompt, ch, test, history, test["user"], self.history_mode)
                res = self._gen(backend, m, messages, seed)
                row = self._quality_row(m, test, ch, system, messages, history, res, run_index, seed)
                self.w.row(row)
                self.w.tick(model=m["id"], phase="quality", current=test["id"])
                self._echo(row)

    def _run_interactive(self, backend, m, test, ch, run_index, seed) -> None:
        """Long conversation where the model's own replies stay in the history."""
        history = to_messages(test.get("history"))
        for turn, step in enumerate(test["script"], 1):
            messages, system = build_messages(self.base_prompt, ch, test, history, step["user"], self.history_mode)
            res = self._gen(backend, m, messages, seed + turn)
            checks = step.get("checks") or {}
            row = self._quality_row(m, test, ch, system, messages, history, res, run_index, seed + turn, turn=turn,
                                    checks=checks, is_probe=bool(checks) or step.get("probe", False))
            row["expected_behavior"] = step.get("expected_behavior") or test.get("expected_behavior")
            if not self.cfg.get("output", {}).get("save_full_long_history", False) and not row["is_probe"]:
                row["messages"] = None
            self.w.row(row)
            self.w.tick(model=m["id"], phase="quality", current=row["test_id"])
            if row["is_probe"]:
                self._echo(row)
            history = history + [{"role": "user", "content": step["user"]},
                                 {"role": "assistant", "content": res.text or "……"}]

    def _echo(self, row) -> None:
        if self.args.quiet:
            return
        flags = ",".join(row["rule_failed"]) or "ok"
        ttft = row["ttft_ms"] if row["ttft_ms"] is not None else "-"
        print(f"  [{row['test_id']:<28}] ttft={ttft:>7}ms  {flags:<28} {(row['response'] or row['error'] or '')[:60]!r}",
              flush=True)

    def perf_messages(self, tests_all) -> list[list[dict[str, str]]]:
        ids = self.cfg.get("perf", {}).get("test_ids") or []
        pool = [t for t in tests_all if t["id"] in ids] or [t for t in tests_all if not t.get("script")][:5]
        out = []
        for t in pool:
            ch = resolve_character(t, self.characters)
            msgs, _ = build_messages(self.base_prompt, ch, t, to_messages(t.get("history")), t["user"], self.history_mode)
            out.append(msgs)
        return out

    def run_perf(self, backend, m, prompts, levels) -> list[dict[str, Any]]:
        pcfg = self.cfg.get("perf", {})
        n_runs = self.args.perf_runs or pcfg.get("runs", 30)
        rollups = []
        for c in levels:
            total = max(n_runs, c * pcfg.get("min_rounds_per_worker", 3))
            # warm-up at this concurrency (not recorded)
            with ThreadPoolExecutor(max_workers=c) as ex:
                list(ex.map(lambda i: self._gen(backend, m, prompts[i % len(prompts)], 10_000 + i, solo=False),
                            range(max(c, self.args.warmup or pcfg.get("warmup", 3)))))
            rows: list[dict[str, Any]] = []
            t0 = time.perf_counter()

            def one(i: int) -> dict[str, Any]:
                seed = self.cfg.get("seed", 1234) + 50_000 + i
                res = self._gen(backend, m, prompts[i % len(prompts)], seed, solo=(c == 1))
                row = base_row(self.w.dir.name, m, self.sampling) | {
                    "phase": "perf", "test_id": f"perf_c{c}_{i:03d}", "category": "perf", "concurrency": c,
                    "run_index": i, "seed": seed, "prompt_index": i % len(prompts),
                    "timestamp": datetime.now().isoformat(timespec="milliseconds"), **result_fields(res)}
                self.w.row(row)
                self.w.tick(model=m["id"], phase=f"perf c={c}", current=row["test_id"])
                return row

            with ThreadPoolExecutor(max_workers=c) as ex:
                for fut in as_completed([ex.submit(one, i) for i in range(total)]):
                    rows.append(fut.result())
            wall = time.perf_counter() - t0
            roll = perf_rollup(rows, wall, m["id"], c)
            self.w.row(roll, "perf.jsonl")
            rollups.append(roll)
            self.w.log(f"  perf c={c:<3} n={total:<4} req/s={roll['requests_per_sec']:.2f} "
                       f"agg tok/s={roll['agg_tokens_per_sec']:.1f} ttft p50={roll['ttft_ms_p50']} p95={roll['ttft_ms_p95']} "
                       f"errors={roll['errors']}")
        return rollups


def count_quality_steps(tests, runs) -> int:
    return runs * sum(len(t["script"]) if t.get("script") else 1 for t in tests)


# ------------------------------------------------------------------ judge
def run_judge(run_dir: Path, cfg: dict[str, Any], models: dict[str, dict[str, Any]], writer: RunWriter | None,
              force: bool = False) -> None:
    jcfg = cfg.get("judge") or {}
    path = run_dir / "rows.jsonl"
    rows = read_jsonl(path)
    judge = LLMJudge(jcfg)
    judge.check_separation([models[m] for m in {r["model"] for r in rows} if m in models])
    if not judge.backend.health():
        print(f"!! judge backend not reachable ({jcfg.get('base_url')}); skipping judge", flush=True)
        return
    max_runs = jcfg.get("max_runs") or 10**9   # judge only the first N repetitions of each test
    todo = [i for i, r in enumerate(rows) if r.get("phase") == "quality" and r.get("is_probe", True)
            and r.get("run_index", 0) < max_runs
            and not r.get("error") and r.get("messages") and (force or not r.get("judge_score"))]
    print(f"== judge: {len(todo)} responses with {jcfg.get('model')}", flush=True)
    if writer:
        writer.progress.update(total=writer.progress["done"] + len(todo), phase="judge", model=jcfg.get("model"))

    def one(i: int) -> None:
        r = rows[i]
        msgs = r["messages"]
        r["judge_score"] = judge.score(msgs[0]["content"], msgs[1:], r["response"], r)
        r["judge_model"] = jcfg.get("model")
        if writer:
            writer.tick(current=r["test_id"])

    with ThreadPoolExecutor(max_workers=jcfg.get("concurrency", 2)) as ex:
        list(ex.map(one, todo))
    judge.backend.release(judge.model)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)
    meta_path = run_dir / "run_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        meta["judge"] = {k: v for k, v in jcfg.items() if k not in ("api_key",)} | {"info": judge.backend.info(judge.model)}
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str))


# ------------------------------------------------------------------ rescore
def rescore(run_dir: Path, cfg: dict[str, Any]) -> int:
    """Re-run the rule-based evaluator on stored responses (after rules or scenario checks change)."""
    sdir = ROOT / cfg.get("scenario_dir", "scenarios")
    tests = {t["id"]: t for t in load_tests(sdir, "all")}
    chars = load_characters(sdir / "characters.yaml")
    path = run_dir / "rows.jsonl"
    rows = read_jsonl(path)
    n = 0
    for r in rows:
        t = tests.get(r.get("base_test_id"))
        if r.get("phase") != "quality" or r.get("error") or t is None:
            continue
        checks = (t["script"][r["turn"] - 1].get("checks") or {}) if r.get("turn") else t.get("checks")
        msgs = r.get("messages")
        ev = rules.evaluate(r["response"] or "", {**t, "checks": checks}, resolve_character(t, chars),
                            cfg.get("rules", {}), history=msgs[1:-1] if msgs else None,
                            weights=cfg.get("rule_weights"), finish_reason=r.get("finish_reason"))
        if not msgs and (r.get("rule_score") or {}).get("repetition"):
            ev["flags"]["repetition"] = True  # history-based repeat check needs messages; keep stored verdict
            ev["failed"] = [k for k, v in ev["flags"].items() if v]
            w = {**rules.DEFAULT_WEIGHTS, **(cfg.get("rule_weights") or {})}
            ev["rule_total"] = round(max(0.0, 1.0 - sum(w[k] for k in ev["failed"])), 3)
        r.update(rule_score=ev["flags"], rule_failed=ev["failed"], rule_total=ev["rule_total"], rule_notes=ev["notes"],
                 has_halluc_check=bool((checks or {}).get("forbidden") or (checks or {}).get("false_premise")))
        n += 1
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)
    print(f"rescored {n} rows in {run_dir}")
    return n


# ------------------------------------------------------------------ merge
def merge_runs(dirs: list[Path], run_id: str, cfg: dict[str, Any], models: dict[str, dict[str, Any]]) -> Path:
    """Combine several runs (e.g. models benchmarked on different days) into one report."""
    from report import build_report
    out = ROOT / cfg.get("output", {}).get("results_dir", "results") / run_id
    out.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {}
    resources: dict[str, Any] = {}
    for name in ("rows.jsonl", "perf.jsonl"):
        with open(out / name, "w", encoding="utf-8") as f:
            for d in dirs:
                for r in read_jsonl(d / name):
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for d in dirs:
        if (d / "resources.json").exists():
            resources |= json.loads((d / "resources.json").read_text())
        if (d / "run_meta.json").exists():
            m = json.loads((d / "run_meta.json").read_text())
            models_meta = meta.get("models", {}) | m.get("models", {})
            meta = {**m, **meta, "models": models_meta}
    meta["merged_from"] = [str(d) for d in dirs]
    (out / "resources.json").write_text(json.dumps(resources, ensure_ascii=False, indent=2))
    (out / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
    return build_report(out, cfg, models)


# ------------------------------------------------------------------ main
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--models-file", default=str(ROOT / "models.yaml"))
    p.add_argument("--models", default="all", help="comma-separated model ids from models.yaml, or 'all'")
    p.add_argument("--scenario", default=None, help="all | comma list of scenario files (knowledge_boundary,memory,...)")
    p.add_argument("--tests", default=None, help="comma list of test ids or categories to restrict to")
    p.add_argument("--runs", type=int, default=None, help="repetitions of each quality test")
    p.add_argument("--concurrency", default=None, help="perf benchmark concurrency levels, e.g. 1,2,4,8,16")
    p.add_argument("--perf-runs", type=int, default=None, help="measured requests per concurrency level (>=30)")
    p.add_argument("--warmup", type=int, default=None)
    p.add_argument("--mode", choices=["auto", "quality", "perf", "both"], default="auto")
    judge = p.add_mutually_exclusive_group()
    judge.add_argument("--judge", dest="judge", action="store_true", default=None, help="run LLM-as-a-Judge")
    judge.add_argument("--no-judge", dest="judge", action="store_false", help="rule-based evaluation only")
    p.add_argument("--judge-only", metavar="RUN_DIR", help="(re)judge an existing run, then rebuild the report")
    p.add_argument("--report-only", metavar="RUN_DIR", help="rebuild the report of an existing run")
    p.add_argument("--rescore", metavar="RUN_DIR", help="re-apply rule-based checks to stored responses, then report")
    p.add_argument("--merge", metavar="RUN_DIRS", help="comma list of run dirs to combine into --run-id, then report")
    p.add_argument("--run-id", default=None)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> Path:
    args = parse_args(argv)
    cfg = load_config(args.config)
    models = load_models(args.models_file)
    from report import build_report  # matplotlib import is slow; keep it lazy

    if args.rescore:
        rescore(Path(args.rescore), cfg)
        return build_report(Path(args.rescore), cfg, models)
    if args.merge:
        return merge_runs([Path(d) for d in args.merge.split(",")], args.run_id or "merged", cfg, models)
    if args.report_only:
        return build_report(Path(args.report_only), cfg, models)
    if args.judge_only:
        run_judge(Path(args.judge_only), cfg, models, None, force=True)
        return build_report(Path(args.judge_only), cfg, models)

    mode = args.mode
    if mode == "auto":
        if args.concurrency and not args.scenario:
            mode = "perf"
        elif args.scenario and not args.concurrency:
            mode = "quality"
        else:
            mode = "both"
    levels = [int(x) for x in (args.concurrency or ",".join(map(str, cfg.get("perf", {}).get("concurrency", [1])))).split(",")]
    use_judge = args.judge if args.judge is not None else bool(cfg.get("judge", {}).get("enabled", False))
    use_judge = use_judge and mode in ("quality", "both")

    selected = select_models(models, args.models)
    tests_all = load_tests(ROOT / cfg.get("scenario_dir", "scenarios"), "all")
    tests = load_tests(ROOT / cfg.get("scenario_dir", "scenarios"), args.scenario or "all",
                       args.tests.split(",") if args.tests else None)
    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    writer = RunWriter(ROOT / cfg.get("output", {}).get("results_dir", "results") / run_id)
    bench = Bench(cfg, args, writer)

    runs = args.runs or cfg.get("quality", {}).get("runs", 1)
    pcfg = cfg.get("perf", {})
    n_perf = sum(max(args.perf_runs or pcfg.get("runs", 30), c * pcfg.get("min_rounds_per_worker", 3)) for c in levels)
    writer.progress["total"] = len(selected) * ((count_quality_steps(tests, runs) if mode != "perf" else 0)
                                                + (n_perf if mode != "quality" else 0))
    writer.log(f"run {run_id}: mode={mode} models={[m['id'] for m in selected]} tests={len(tests)} runs={runs} "
               f"concurrency={levels if mode != 'quality' else '-'} judge={use_judge}")

    meta: dict[str, Any] = {
        "run_id": run_id, "timestamp": datetime.now().isoformat(), "mode": mode, "argv": sys.argv,
        "sampling": bench.sampling, "seed": cfg.get("seed"), "history_mode": bench.history_mode,
        "npc_system_prompt": bench.base_prompt, "npc_system_prompt_sha": sha(bench.base_prompt),
        "scenarios": sorted({t["scenario_file"] for t in tests}), "n_tests": len(tests), "quality_runs": runs,
        "concurrency": levels, "system": system_info(), "python": sys.version, "platform": platform.platform(),
        "scenario_sha": {p.name: sha(p.read_text()) for p in sorted((ROOT / cfg.get("scenario_dir", "scenarios")).glob("*.yaml"))},
        "config": cfg, "models": {},
    }
    writer.json("run_meta.json", meta)
    resources: dict[str, Any] = {}

    for m in selected:
        writer.log(f"== {m['id']} ({m['backend']}: {m['model']})")
        backend = create_backend(m, timeout=cfg.get("request_timeout", 300))
        server = ManagedServer(m, writer.dir / f"server_{m['id']}.log")
        try:
            if server.cfg.get("cmd") and cfg.get("gpu_hygiene", {}).get("unload_ollama_before_launch", True):
                unload_ollama(cfg.get("gpu_hygiene", {}).get("ollama_url", "http://localhost:11434"))
            server.start(backend, writer)
            if not backend.health():
                raise ConnectionError(f"backend not reachable at {backend.base_url}")
            backend.prepare(m["model"])
            mon = ResourceMonitor(gpu_indices=cfg.get("gpu_indices"), interval=cfg.get("monitor_interval", 0.2),
                                  process_match=m.get("process_match"), pids=server.pids)
            mon.start()
            # warm-up (loads the model in on-demand servers like Ollama)
            warm_msgs = bench.perf_messages(tests_all)
            for i in range(args.warmup or cfg.get("quality", {}).get("warmup", 3)):
                r = bench._gen(backend, m, warm_msgs[i % len(warm_msgs)], 999 + i)
                if r.error:
                    raise RuntimeError(f"warm-up failed: {r.error}")
            rev = m.get("revision") or hf_revision(m["model"])
            meta["models"][m["id"]] = {k: v for k, v in m.items() if k not in ("api_key",)} | {
                "server": backend.info(m["model"]), "revision": rev,
                "tokenizer_revision": m.get("tokenizer_revision") or rev}
            writer.json("run_meta.json", meta)
            if mode in ("quality", "both"):
                bench.run_quality(backend, m, tests)
            if mode in ("perf", "both"):
                bench.run_perf(backend, m, warm_msgs, levels)
            mon.stop()
            resources[m["id"]] = mon.summary(backend.resource_info(m["model"]))
            writer.log(f"  VRAM {resources[m['id']].get('vram_peak_mb', 0) / 1024:.2f} GB "
                       f"({resources[m['id']].get('vram_method')}), RAM {resources[m['id']].get('ram_peak_mb', 0) / 1024:.2f} GB")
        except Exception as e:  # noqa: BLE001
            writer.log(f"!! {m['id']} failed: {type(e).__name__}: {e}")
            meta["models"].setdefault(m["id"], dict(m))["failed"] = str(e)
        finally:
            backend.release(m["model"])
            server.stop()
            backend.close()
        writer.json("resources.json", resources)
        writer.json("run_meta.json", meta)

    if use_judge:
        try:
            run_judge(writer.dir, cfg, models, writer)
        except Exception as e:  # noqa: BLE001
            writer.log(f"!! judge failed: {type(e).__name__}: {e}")
    writer.progress["phase"] = "report"
    out = build_report(writer.dir, cfg, models)
    writer.progress.update(status="done", phase="done", report=str(out))
    writer._flush()
    print(f"\nresults: {writer.dir}\nreport:  {out / 'report.html'}")
    return out


if __name__ == "__main__":
    main()
