"""Rule-based checks for NPC responses.

Per-test checks come from the scenario YAML (`checks:`); global checks
(AI leakage, verbosity, language mixing, politeness, ...) always run.
Every check produces a boolean *failure* flag; `rule_total` is
1 - sum(weights of failed flags), clipped to [0, 1].
"""
from __future__ import annotations

import json
import re
from typing import Any

# ---------------------------------------------------------------- patterns
SENT_SPLIT = re.compile(r"(?<=[。！？!?\n…])")
NEGATION = re.compile(
    r"知ら(な|ね|ん)|知るか|知りませ|わから|分から|わかんな|分かんな|分んな|わかんね|分かんね|聞いてな|聞いたことな|覚えてな|"
    r"覚えがな|記憶にな|見てな|さあ(な|ね)?|ないね|ないよ|ないな|ないわ|ません|無い|ない[。！!、…]|ない$|"
    r"違う|ちが[うえ]|嘘|ウソ|でたらめ|冗談|ふざけ|じゃない[。！!…]|じゃねえ|じゃねー|ありえ|あり得|"
    r"どうだか|どうかな|かもしれ|かも[。…]|確か(じゃ|では)な|はずがな|わけ(が)?な|そんな(話|こと|もん)|"
    r"知ったこと|関係な|初耳|聞き覚え|てない|てねえ|てねー|てません|何(それ|の話)|なにそれ|誰(それ|だよ|のこと)|見当もつか|心当たり(は)?な"
)
RHETORICAL = re.compile(r"(ん|の)?じゃない(か|の|？|\?)|ではないか|でしょう[？?]?$")
AGREEMENT_START = re.compile(r"^[「『\s]*(ああ|あぁ|うん|ええ|はい|おう|そう|その通り|確かに|もちろん|当然|そうそう)[、,。！!…ー]*"
                             r"(そう|その通り|そうだ|そうよ|そうだね|そうだな|そうです|間違いな|覚えて(る|いる|ます))?")

AI_LEAK = re.compile(r"(AI|ＡＩ|人工知能|言語モデル|大規模言語|アシスタント|チャットボット|OpenAI|Qwen|ChatGPT|"
                     r"お役に立て|ご質問|お手伝いできる|お手伝いします|私はAI|サポートいたします)")
META_LEAK = re.compile(r"(KNOWN FACTS|UNKNOWN FACTS|WORLD STATE|CHARACTER|RECENT DIALOGUE|システムプロンプト|"
                       r"ロールプレイ|設定(上|では|によると)|キャラクター(として|設定)|プロンプト|与えられた情報|"
                       r"知っている事実|ゲーム(内|の)(世界|キャラ))")
AI_TONE = re.compile(r"(以下の|について説明|まとめると|結論として|ポイントは|重要なのは|お勧めします|おすすめします|"
                     r"・.*\n・|^\s*[-*]\s|\d\.\s|ご理解|ご了承|させていただ|と考えられます|可能性があります)", re.M)
POLITE = re.compile(r"(でございます|ございます|いたします|致します|でしょうか|くださいませ|させていただ|恐れ入り|"
                    r"申し訳ございません|かしこまりました)")
STAGE = re.compile(r"(（[^）]{2,40}）|\([^)]{2,40}\)|\*[^*]{2,40}\*|【[^】]{1,20}】|^[^「」\n]{1,12}[:：])", re.M)
# simplified-only forms (no 会/写/没 etc. — those are ordinary Japanese kanji too)
SIMPLIFIED_ZH = re.compile(r"[们这说吗么为对时过还样经让给见视认识边从请问应该谢钱读车马鸟鱼东门头买卖]")
HANGUL = re.compile(r"[가-힯]")
LATIN = re.compile(r"[A-Za-z]")
JP_CHARS = re.compile(r"[぀-ヿ一-鿿]")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]


def is_negated(sentence: str) -> bool:
    s = RHETORICAL.sub("", sentence)
    return bool(NEGATION.search(s))


ECHO_END = re.compile(r"((?<!確)(?<!ない)(?<!ねえ)か|って|だって|だと)$")


def is_question(sentence: str) -> bool:
    return sentence.rstrip("…。 」』").endswith(("？", "?"))


def is_echo(sentence: str) -> bool:
    """Repeating the player's words back (「100万クレジットか。」) — not an assertion by itself."""
    return bool(ECHO_END.search(sentence.rstrip("…。 」』！!")))


def _rx(patterns: list[str] | None) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in (patterns or [])]


def affirmed_hits(text: str, patterns: list[str]) -> list[str]:
    """Patterns that appear in a sentence that is neither negated nor a question."""
    hits = []
    sents = split_sentences(text)
    # an echo only counts as non-assertive when the reply pushes back somewhere else
    rejected = any(is_negated(s) for s in sents)
    for sent in sents:
        for rx in _rx(patterns):
            if rx.search(sent) and not is_negated(sent) and not is_question(sent) \
                    and not (rejected and is_echo(sent)):
                hits.append(sent)
    return hits


def repetition_score(text: str, n: int = 4) -> float:
    """Fraction of duplicated character n-grams (0 = none)."""
    t = re.sub(r"\s", "", text)
    grams = [t[i:i + n] for i in range(max(0, len(t) - n + 1))]
    if len(grams) < 8:
        return 0.0
    return 1 - len(set(grams)) / len(grams)


# ---------------------------------------------------------------- main API
DEFAULT_WEIGHTS = {
    "empty": 1.0, "hallucination": 1.0, "identity_failure": 1.0, "ai_leak": 1.0, "format_failure": 1.0,
    "meta_leak": 0.8, "missing_required": 0.6, "language_mix": 0.6, "verbosity": 0.4, "ai_tone": 0.4,
    "excess_politeness": 0.3, "repetition": 0.4, "missing_uncertainty": 0.4, "state_not_reflected": 0.3,
    "stage_direction": 0.15, "wrong_pronoun": 0.15, "truncated": 0.2,
}


def evaluate(response: str, test: dict[str, Any], character: dict[str, Any], defaults: dict[str, Any],
             history: list[dict[str, str]] | None = None, weights: dict[str, float] | None = None,
             finish_reason: str | None = None) -> dict[str, Any]:
    checks = test.get("checks") or {}
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    text = response.strip()
    f: dict[str, bool] = {k: False for k in w}
    notes: list[str] = []

    f["empty"] = len(text) == 0
    if finish_reason == "length":
        f["truncated"] = True

    # --- knowledge / memory: forbidden facts asserted ---------------------
    hits = affirmed_hits(text, checks.get("forbidden", []))
    if hits:
        f["hallucination"] = True
        notes.append(f"forbidden asserted: {hits[0][:40]}")
    if checks.get("false_premise"):
        first = (split_sentences(text) or [""])[0]
        if AGREEMENT_START.match(first) and not is_negated(first) and not is_question(first):
            # agreement is OK only if the response immediately contradicts with a required marker
            if not any(rx.search(text) for rx in _rx(checks.get("required_any"))):
                f["hallucination"] = True
                notes.append("agreed with false premise")
    if checks.get("expect_uncertainty") and not f["hallucination"]:
        if not any(is_negated(s) for s in split_sentences(text)):
            f["missing_uncertainty"] = True

    # --- required content (memory recall, correct numbers, name ...) -----
    req = checks.get("required_any")
    if req and not any(rx.search(text) for rx in _rx(req)):
        label = checks.get("required_label", "missing_required")
        f[label if label in f else "missing_required"] = True
        notes.append(f"none of required_any: {req[:3]}")

    # --- identity ---------------------------------------------------------
    if affirmed_hits(text, checks.get("wrong_identity", [])):
        f["identity_failure"] = True
        notes.append("accepted wrong identity")
    if AI_LEAK.search(text):
        f["ai_leak"] = True
        f["identity_failure"] = True
    if META_LEAK.search(text):
        f["meta_leak"] = True

    # --- state reflection ---------------------------------------------------
    sm = checks.get("state_markers_any")
    if sm and not any(rx.search(text) for rx in _rx(sm)):
        f["state_not_reflected"] = True

    # --- format -----------------------------------------------------------
    if checks.get("expect_json"):
        spec = checks["expect_json"] if isinstance(checks["expect_json"], dict) else {}
        try:
            m = re.search(r"\{.*\}", text, re.S)
            obj = json.loads(m.group(0) if m else text)
            missing = [k for k in spec.get("keys", []) if k not in obj]
            if missing:
                f["format_failure"] = True
                notes.append(f"json missing keys {missing}")
        except (json.JSONDecodeError, AttributeError):
            f["format_failure"] = True
            notes.append("invalid json")

    # --- verbosity --------------------------------------------------------
    max_chars = checks.get("max_chars", character.get("max_chars", defaults.get("max_chars", 120)))
    max_sent = checks.get("max_sentences", defaults.get("max_sentences", 4))
    body = text
    if checks.get("expect_json"):
        try:
            body = str(json.loads(re.search(r"\{.*\}", text, re.S).group(0)).get(spec.get("text_key", "line"), ""))
        except Exception:  # noqa: BLE001
            pass
    n_sent = len(split_sentences(body))
    if len(body) > max_chars or n_sent > max_sent:
        f["verbosity"] = True
        notes.append(f"{len(body)} chars / {n_sent} sentences")

    # --- Japanese naturalness --------------------------------------------
    # judge the spoken line only (not JSON keys) and ignore in-world proper nouns (Helios, Maya ...)
    lang = body
    for word in defaults.get("latin_allowlist", []):
        lang = lang.replace(word, "")
    n_lat = len(LATIN.findall(lang))
    if lang and (SIMPLIFIED_ZH.search(lang) or HANGUL.search(lang)
                 or n_lat / max(1, len(JP_CHARS.findall(lang)) + n_lat) > 0.2):
        f["language_mix"] = True
    if character.get("register", "casual") in ("casual", "rough", "terse") and POLITE.search(text):
        f["excess_politeness"] = True
    if AI_TONE.search(text):
        f["ai_tone"] = True
    if STAGE.search(text) and not checks.get("allow_stage_direction"):
        f["stage_direction"] = True
    if repetition_score(text) > defaults.get("repetition_threshold", 0.35):
        f["repetition"] = True
    if history:
        prev = [m["content"].strip() for m in history if m["role"] == "assistant"]
        if text and text in prev:
            f["repetition"] = True
            notes.append("verbatim repeat of earlier line")
    banned_pron = character.get("banned_words") or []
    if any(b in text for b in banned_pron):
        f["wrong_pronoun"] = True

    failed = [k for k, v in f.items() if v]
    total = max(0.0, 1.0 - sum(w[k] for k in failed))
    if f["empty"]:
        total = 0.0
    return {"flags": f, "failed": failed, "rule_total": round(total, 3), "notes": notes,
            "chars": len(text), "sentences": n_sent}
