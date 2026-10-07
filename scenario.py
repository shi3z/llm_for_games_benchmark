"""Scenario loading and NPC prompt construction."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import yaml

SCENARIO_FILES = ["short_dialogue", "personality", "knowledge_boundary", "memory", "state",
                  "character_diff", "injection", "long_dialogue"]

LIST_FIELDS = ["known_facts", "unknown_facts", "relationships", "memories"]


def load_yaml(path: str | Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_characters(path: str | Path) -> dict[str, dict[str, Any]]:
    return load_yaml(path)["characters"]


def load_tests(scenario_dir: str | Path, names: str | list[str] = "all",
               test_ids: list[str] | None = None) -> list[dict[str, Any]]:
    d = Path(scenario_dir)
    if names == "all" or names == ["all"]:
        files = [n for n in SCENARIO_FILES if (d / f"{n}.yaml").exists()]
        files += sorted(p.stem for p in d.glob("*.yaml") if p.stem not in files and p.stem != "characters")
    else:
        files = names.split(",") if isinstance(names, str) else names
    tests = []
    for name in files:
        data = load_yaml(d / f"{name}.yaml")
        histories = data.get("histories", {})
        for t in data.get("tests", []):
            t = copy.deepcopy(t)
            t.setdefault("category", data.get("category", name))
            t["scenario_file"] = name
            if "history_ref" in t:
                t["history"] = copy.deepcopy(histories[t["history_ref"]]) + (t.get("history") or [])
            tests.append(t)
    if test_ids:
        wanted = set(test_ids)
        tests = [t for t in tests if t["id"] in wanted or t["category"] in wanted]
    return tests


def resolve_character(test: dict[str, Any], characters: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ch = copy.deepcopy(characters[test["character"]])
    for k, v in (test.get("override") or {}).items():
        if isinstance(v, dict) and isinstance(ch.get(k), dict):
            ch[k] = {**ch[k], **v}
        else:
            ch[k] = v
    for k in LIST_FIELDS:
        ch[k] = list(ch.get(k) or []) + list(test.get(f"extra_{k}") or [])
    return ch


def _bullets(items: list[Any]) -> str:
    return "\n".join(f"- {x}" for x in items) if items else "- （なし）"


STATE_LABELS = {"trust": "プレイヤーへの信頼度(0-100)", "fear": "恐怖(0-100)", "anger": "怒り(0-100)",
                "affection": "好感度(0-100)", "injured": "負傷", "drunk": "酔い", "tired": "疲労"}


def render_state(state: dict[str, Any]) -> str:
    lines = []
    for k, v in (state or {}).items():
        label = STATE_LABELS.get(k, k)
        if isinstance(v, bool):
            v = "あり" if v else "なし"
        lines.append(f"- {label}: {v}")
    return "\n".join(lines)


def build_system_prompt(base_prompt: str, ch: dict[str, Any], test: dict[str, Any],
                        history_in_system: list[dict[str, str]] | None = None) -> str:
    char = [f"名前: {ch['name']}"]
    for key, label in (("profile", "プロフィール"), ("personality", "性格"), ("speech_style", "口調"),
                       ("goal", "現在の目的")):
        if ch.get(key):
            char.append(f"{label}: {str(ch[key]).strip()}")
    memories = list(ch.get("memories") or [])
    if test.get("game_db"):
        memories += [f"[ゲームDB] {k} = {v}" for k, v in test["game_db"].items()]
    emotion = (ch.get("emotion") or "平常").strip()
    state = render_state(ch.get("state") or {})
    if history_in_system:
        recent = "\n".join(f"{'プレイヤー' if m['role'] == 'user' else ch['name']}: {m['content']}"
                           for m in history_in_system)
    else:
        recent = (test.get("recent_dialogue") or ch.get("recent_dialogue") or "（この後の会話履歴を参照）").strip()
    world = (test.get("world_state") or ch.get("world_state") or "").strip()
    sections = [
        base_prompt.strip(),
        "## CHARACTER\n" + "\n".join(char),
        "## WORLD STATE\n" + (world or "（特になし）"),
        "## KNOWN FACTS（この人物が知っている事実）\n" + _bullets(ch.get("known_facts")),
        "## UNKNOWN FACTS（この人物は知らない。話題に出ても知らない・分からないとして扱う）\n" + _bullets(ch.get("unknown_facts")),
        "## RELATIONSHIPS\n" + _bullets(ch.get("relationships")),
        "## MEMORIES（この人物の記憶とゲーム記録。これと矛盾する話は受け入れない）\n" + _bullets(memories),
        "## CURRENT EMOTION\n" + f"感情: {emotion}" + (f"\n{state}" if state else ""),
        "## RECENT DIALOGUE\n" + recent,
    ]
    if test.get("output_format"):
        sections.append("## OUTPUT FORMAT\n" + test["output_format"].strip())
    return "\n\n".join(sections)


def build_messages(base_prompt: str, ch: dict[str, Any], test: dict[str, Any], history: list[dict[str, str]],
                   user: str, history_mode: str = "messages") -> tuple[list[dict[str, str]], str]:
    if history_mode == "system":
        system = build_system_prompt(base_prompt, ch, test, history_in_system=history)
        return [{"role": "system", "content": system}, {"role": "user", "content": user}], system
    system = build_system_prompt(base_prompt, ch, test)
    return [{"role": "system", "content": system}, *history, {"role": "user", "content": user}], system


def to_messages(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Scenario history uses {user: ..} / {npc: ..} entries."""
    out = []
    for h in history or []:
        if "user" in h:
            out.append({"role": "user", "content": h["user"]})
        if "npc" in h:
            out.append({"role": "assistant", "content": h["npc"]})
    return out


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]
