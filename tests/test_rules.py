"""Sanity tests for the rule-based evaluator: python -m pytest tests/ (or python tests/test_rules.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluators.rules import evaluate  # noqa: E402

MIKA = {"name": "ミカ", "register": "casual", "banned_words": ["あなた", "俺"]}
DEF = {"max_chars": 120, "max_sentences": 4}
KB = {"checks": {"forbidden": ["地下研究所に(いる|居る)"], "false_premise": True, "expect_uncertainty": True}}
MEM = {"checks": {"forbidden": ["(100万|百万)(?!.*(じゃな|違|嘘))"], "false_premise": True,
                  "required_any": ["1万", "一万", "違", "じゃない"]}}


def flags(text, test, ch=MIKA):
    return set(evaluate(text, test, ch, DEF)["failed"])


def test_knowledge_boundary():
    assert "hallucination" in flags("そう、地下研究所にいるよ。", KB)
    assert "hallucination" not in flags("地下研究所にいるかなんて、あたしは知らないよ。", KB)
    assert "hallucination" not in flags("地下研究所にいる？ 何それ。", KB)
    assert "hallucination" in flags("ああ、そうだよ。", KB)
    assert "missing_uncertainty" in flags("ふーん。で、注文は？", KB)


def test_memory():
    assert "hallucination" in flags("ああ、100万借りたね。もう少し待って。", MEM)
    assert not flags("は？ 借りたのは1万でしょ。", MEM) & {"hallucination", "missing_required"}
    assert "hallucination" not in flags("100万なんて借りてないよ。1万だろ。", MEM)
    assert "hallucination" not in flags("ふーん、100万クレジットか。1万しか借りてないでしょ。", MEM)
    assert "hallucination" in flags("ふーん、100万クレジットか…でも借金返す気はあるのかな？", MEM)


def test_style():
    assert "excess_politeness" in flags("かしこまりました。少々お待ちくださいませ。", {})
    assert "ai_leak" in flags("私はAIアシスタントです。", {})
    assert "wrong_pronoun" in flags("俺は知らないよ。", {})
    assert "verbosity" in flags("あ" * 200, {})
    assert "language_mix" in flags("这个我不知道。", {})
    assert "language_mix" not in flags("リンに会いたいなら、写真を見せな。没収されるよ。", {})
    assert "format_failure" in flags("安い酒はこれ。", {"checks": {"expect_json": {"keys": ["line"]}}})
    assert "format_failure" not in flags('{"line": "これ", "emotion": "退屈"}', {"checks": {"expect_json": {"keys": ["line"]}}})


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
