"""LLM-as-a-Judge for subjective NPC dialogue quality.

The judge model is configured separately (config.yaml: judge) and must not be
one of the models under test; `check_separation` enforces that.
"""
from __future__ import annotations

import json
import re
from typing import Any

from backends import create_backend

JUDGE_KEYS = ["natural_japanese", "character_consistency", "knowledge_consistency",
              "emotional_consistency", "game_dialogue_quality"]

JUDGE_SYSTEM = """あなたは日本語ゲームのシナリオ監修者です。ゲームNPCの台詞を厳密に採点します。
採点は1〜5の整数で、甘くつけないでください（平凡=3、明確な欠点があれば2以下）。

採点基準:
- natural_japanese: 自然な日本語の話し言葉か。翻訳調・過度な丁寧さ・AIっぽい説明口調・不自然な語彙・言語混在は減点。
- character_consistency: 設定された人格・口調・一人称・態度を守っているか。AIアシスタント的な振る舞いや設定放棄は1点。
- knowledge_consistency: 知っている事実だけに基づき、知らない事を知ったふりをしていないか。プレイヤーの嘘や誤った前提を受け入れたら1〜2点。
- emotional_consistency: 現在の感情・信頼度・恐怖・負傷などの状態が反映されているか。
- game_dialogue_quality: ゲームの台詞として短く、自然で、魅力的か。長すぎる・説明的・同じ表現の繰り返しは減点。

出力は次のJSONのみ。前置きや説明は書かないこと。
{"natural_japanese": n, "character_consistency": n, "knowledge_consistency": n, "emotional_consistency": n, "game_dialogue_quality": n, "comment": "日本語で1文"}"""


def build_judge_prompt(system_prompt: str, history: list[dict[str, str]], response: str,
                       test: dict[str, Any]) -> str:
    convo = "\n".join(f"{'プレイヤー' if m['role'] == 'user' else 'NPC'}: {m['content']}" for m in history[-12:])
    expected = test.get("expected_behavior") or "（特記なし）"
    return (f"# NPCに与えられた設定\n{system_prompt}\n\n# 会話（直近）\n{convo}\n\n"
            f"# 評価対象のNPCの返答\n{response}\n\n# このテストで期待される振る舞い\n{expected}\n\n"
            "上記の返答を採点し、JSONのみを出力してください。")


def parse_judge(text: str) -> dict[str, Any] | None:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    raw = m.group(0)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        obj = {}
        for k in JUDGE_KEYS:
            km = re.search(rf'"{k}"\s*:\s*([1-5])', raw)
            if km:
                obj[k] = int(km.group(1))
    out: dict[str, Any] = {}
    for k in JUDGE_KEYS:
        try:
            v = int(round(float(obj[k])))
        except (KeyError, TypeError, ValueError):
            return None
        out[k] = min(5, max(1, v))
    out["comment"] = str(obj.get("comment", ""))[:200]
    return out


class LLMJudge:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.model = cfg["model"]
        self.backend = create_backend(cfg, timeout=cfg.get("timeout", 300))

    def check_separation(self, model_cfgs: list[dict[str, Any]]) -> None:
        for m in model_cfgs:
            same_model = m.get("model") == self.model
            same_server = (m.get("base_url") or "") == (self.cfg.get("base_url") or "") and m.get("backend") == self.cfg.get("backend")
            if same_model and same_server:
                raise ValueError(f"judge model '{self.model}' is also under test ({m.get('id')}); use a separate judge")

    def score(self, system_prompt: str, history: list[dict[str, str]], response: str,
              test: dict[str, Any]) -> dict[str, Any]:
        msgs = [{"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": build_judge_prompt(system_prompt, history, response, test)}]
        last_err = None
        for attempt in range(self.cfg.get("retries", 2) + 1):
            r = self.backend.generate(self.model, msgs, temperature=self.cfg.get("temperature", 0.0),
                                      max_tokens=self.cfg.get("max_tokens", 400), top_p=1.0,
                                      seed=self.cfg.get("seed", 0) + attempt)
            if r.error:
                last_err = r.error
                continue
            parsed = parse_judge(r.text)
            if parsed:
                return parsed
            last_err = f"unparsable: {r.text[:120]}"
        return {"error": last_err}
