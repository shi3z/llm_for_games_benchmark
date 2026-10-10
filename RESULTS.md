# 検証結果（2026-10-07 / 2026-10-10更新）

日本語ゲームNPC用途で、7B〜14Bクラスを中心にローカルLLM 11設定（および最新世代の Gemma 4 E4B / 12B）を比較した結果です。
詳細なレポート（グラフ・同時実行スケーリング・同じ会話への全モデルの返答の横並び比較）は GitHub 上でそのまま読める [reports/all_models_v2/report.md](reports/all_models_v2/report.md)（HTML版: [report.html](reports/all_models_v2/report.html)）、
全試行の生データは [results/all_models_v2/rows.jsonl](results/all_models_v2/rows.jsonl) にあります。

## 条件

- GPU: NVIDIA GeForce RTX 4090 24GB（driver 590.48.01）。ComfyUI等の他プロセスと共有（計測時は約4GB使用）
  - ※ Gemma 4 E4B / 12B は NVIDIA GB10（DGX Spark、統一メモリ 128GB）で計測。
- サンプリング: temperature 0.7 / top_p 0.9 / max_tokens 128、seed固定（全モデル共通）
- 品質: 全73テスト × 3〜5回。ルール評価は全試行、LLM Judge（gpt-oss:20b）は最初の2回分
- 負荷: 同時実行 1 / 2 / 4 / 8 / 16、各30〜48リクエスト（ウォームアップ後）
- Qwen3/Gemma4系は思考モードOFF。Qwen3-14B BF16は24GBに載らないため対象外
- 速度系の値は perf（同時実行1）の計測値

## 結果（NPC Score順）

| # | モデル | 品質 | 日本語 | キャラ | 知識 | Halluc. | 長期会話 | TTFT p50/p95 (ms) | tok/s | 最大req/s | VRAM GB | NPC Score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Qwen3-8B (Q4_K_M, Ollama) | 90.1 | 88.1 | 87.9 | 90.8 | 3.4% | 85.5 | 97 / 121 | 152 | 4.9 | 6.3 | **91.4** |
| 2 | Llama-3-ELYZA-JP-8B (GGUF Q4_K_M, llama.cpp) | 90.4 | 87.8 | 88.0 | 94.3 | 7.6% | 83.7 | 24 / 93 | 159 | 11.5 | 12.9 | **90.9** |
| 3 | Qwen3-14B (AWQ, vLLM) | 89.6 | 87.5 | 90.1 | 93.6 | 3.4% | 81.9 | 17 / 19 | 95 | 29.5 | 13.4 | **89.8** |
| 4 | Qwen3-8B (AWQ, vLLM) | 88.1 | 88.9 | 88.9 | 83.6 | 5.5% | 87.2 | 13 / 14 | 148 | 46.0 | 13.5 | **89.3** |
| 5 | Qwen3-14B (GGUF Q4_K_M, llama.cpp) | 90.1 | 90.2 | 90.6 | 92.7 | 1.4% | 80.1 | 42 / 83 | 89 | 9.8 | 14.0 | **89.2** |
| 6 | Gemma3-4B-it (Q4_K_M, Ollama) | 82.0 | 80.1 | 84.2 | 76.6 | 9.0% | 72.8 | 194 / 211 | 222 | 6.7 | 4.2 | **88.3** |
| 7 | Qwen2.5-7B-Instruct (Q4_K_M, Ollama) | 80.9 | 71.8 | 83.6 | 81.5 | 6.2% | 73.8 | 106 / 126 | 179 | 4.0 | 5.3 | **85.6** |
| 8 | Qwen3-14B (FP8, vLLM) | 90.8 | 92.0 | 90.9 | 95.7 | 2.1% | 78.2 | 28 / 45 | 58 | 25.8 | 17.8 | **84.8** |
| 9 | Gemma 4 E4B (Q4_K_M, Ollama) ※ | 92.1 | 92.0 | 95.5 | 95.4 | **1.1%** | 94.5 | 436 / 526 | 62 | 2.3 | 4.9 | **82.8** |
| 10 | Llama-3-ELYZA-JP-8B (BF16, vLLM) | 87.8 | 86.4 | 84.9 | 92.8 | 7.6% | 78.3 | 28 / 44 | 60 | 21.7 | 18.5 | **82.3** |
| 11 | Qwen3.8-9B-Distill (GGUF Q4_K_M, llama.cpp) | 77.7 | 65.8 | 81.4 | 78.4 | 7.6% | 66.7 | 138 / 221 | 137 | 4.2 | 8.1 | **82.1** |
| 12 | Shisa V2 Qwen2.5-7B (BF16, vLLM) | 81.5 | 72.3 | 85.1 | 77.2 | 6.9% | 77.7 | 25 / 27 | 63 | 14.5 | 18.5 | **78.6** |
| 13 | Gemma 4 12B (Q4_K_M, Ollama) ※ | **93.7** | 88.7 | 94.0 | **96.6** | 2.3% | 94.5 | 607 / 779 | 38 | 1.0 | 9.0 | **75.0** |

※ Gemma 4 E4B / 12B は NVIDIA GB10（DGX Spark）で計測。TTFTやtok/sなどの速度指標はマシン環境差（RTX 4090 vs GB10）を含みます。

## ランキング（上位3）

| 部門 | 1位 | 2位 | 3位 |
|---|---|---|---|
| Best Quality | **Gemma 4 12B** (Q4_K_M, Ollama: 93.7) | **Gemma 4 E4B** (Q4_K_M, Ollama: 92.1) | Qwen3-14B (FP8, vLLM: 90.8) |
| Best Latency (TTFT p50) | Qwen3-8B (AWQ, vLLM: 13ms) | Qwen3-14B (AWQ, vLLM: 17ms) | Llama-3-ELYZA-JP-8B (GGUF Q4_K_M: 24ms) |
| Best Japanese | **Gemma 4 E4B** (Ollama: 92.0) / Qwen3-14B (FP8: 92.0) [同率] | Qwen3-14B (GGUF Q4_K_M: 90.2) | Qwen3-8B (AWQ, vLLM: 88.9) |
| Best Character Consistency | **Gemma 4 E4B** (Q4_K_M, Ollama: 95.5) | **Gemma 4 12B** (Q4_K_M, Ollama: 94.0) | Qwen3-14B (FP8, vLLM: 90.9) |
| Lowest Hallucination | **Gemma 4 E4B** (Q4_K_M, Ollama: 1.1%) | Qwen3-14B (GGUF Q4_K_M: 1.4%) | Qwen3-14B (FP8, vLLM: 2.1%) |
| Best VRAM Efficiency (quality / GB) | Gemma3-4B-it (Q4_K_M: 19.5) | **Gemma 4 E4B** (Q4_K_M: 18.9) | Qwen2.5-7B-Instruct (Q4_K_M: 15.3) |
| Best Overall NPC Model | Qwen3-8B (Q4_K_M, Ollama: 91.4) | Llama-3-ELYZA-JP-8B (GGUF Q4_K_M: 90.9) | Qwen3-14B (AWQ, vLLM: 89.8) |

## 所見

- **Gemma 4（2026年新世代）の圧倒的な会話品質**:
  - **Gemma 4 12B** は品質 **93.7**（全モデル中1位）、知識整合性 **96.6%**（1位）と既存の14Bクラスを凌駕する最高精度を達成。
  - **Gemma 4 E4B**（4.5B）はハルシネーション率わずか **1.1%**（全モデル中最少）、キャラ一貫性 **95.5%**（1位）を記録。前世代の Gemma 3 4B-it（品質82.0、ハルシネーション9.0%）から劇的な進化を遂げ、エッジ向けNPCモデルとして最高峰の安定性を示しました。
  - ただし Ollama 上での推論速度（38〜62 tok/s）やTTFTにより、速度・スループットを加味した総合 NPC Score では中位（E4B 9位 / 12B 13位）となります。
- **品質はQwen3-14B系も高水準**（FP8 90.8、ハルシネーション1.4〜2.1%）。
- **速度・同時接続は vLLM が圧倒的**。Qwen3-8B AWQ は16同時でも TTFT p95 50ms・46 req/s。Ollama は同モデルでも約5 req/sで頭打ち（`OLLAMA_NUM_PARALLEL` 依存）。
- **日本語特化モデル**: ELYZA-JP-8B は知識整合性が高いが嘘の前提への同意がやや多い（7.6%）。Shisa V2 7B は返答が長すぎる（37%）、ト書き混入、コードを書く誘導に従う。
- **Qwen2.5-7B** は返答の約11%に英単語が混入（「undergroundのことか？」）。
- **Qwen3.8-9B-Distill**（Ollama `tobestyledintro/qwen3.8-9b-distill`、元ファイル名 `Qwen3.8-9B-Distill-uncensored-heretic`）は品質最下位（77.5）。55%が長すぎ・複数行の「」書き・ト書き。TTFTも llama.cpp 上で他モデルより遅い（p50 138ms）。

## 注意

- **VRAMはサーバーの確保量**。vLLMは `--gpu-memory-utilization` 分、llama.cppは `-c` 分のKVキャッシュを含む。このためvLLMのFP8/BF16モデルはメモリ効率で不利になりNPC Scoreが下がっている（重みは `config.yaml: npc_score_weights` で変更可）。
- Qwen3-14B FP8 は空きVRAMの都合で `--max-model-len 4096`（テストの最長プロンプトは約2,800トークン）、また古いnvccでFlashInferのJITが通らないため `--attention-backend TRITON_ATTN`。
- ルールベース評価は正規表現。検証中に見つかった誤検出（日本語と共通の漢字を簡体字扱い、JSONキーや作中固有名詞の英字、「〜てない」形の否定、オウム返し）を修正し、全行を `--rescore` で同じルールで再採点済み。
- Judge（gpt-oss:20b）は全体に甘め（明確なでっち上げにも高得点をつける例あり）。Gemma 4 の評価はルールベース採点（全試行）をベースとしています。
- **Gemma 4 の計測環境**: Gemma 4 E4B / 12B は NVIDIA GB10（DGX Spark、統一メモリ）で計測しています。GPUアーキテクチャおよび Ollama の設定が異なるため、RTX 4090 で計測した他モデルとTTFT・tok/sなどの速度指標を直接横並び比較する際は留意してください。
- **Gemma 5 について**: 2026年10月現在、Google から未発表・未リリースのため本検証の対象外です（Gemma 4 が最新世代）。

---

# 埋め込みモデルの検証: google/embeddinggemma-2（2026-10-07）

EmbeddingGemma 2 は埋め込み専用で文章を生成できないため、上の生成ベンチ（NPC Score）の対象外です。
代わりに、NPCパイプラインで埋め込みが担う用途を `embed_bench.py` で測りました。

```bash
python embed_bench.py --model google/embeddinggemma-2 --out results/embedding/embeddinggemma-2.json
python embed_bench.py --model google/embeddinggemma-2 --text-only --out results/embedding/embeddinggemma-2-textonly.json
```

## 条件

- GPU: NVIDIA GB10（DGX Spark、統一メモリ）。上の生成ベンチ（RTX 4090）とは別マシンなので、速度は比較できません。
- bfloat16、リビジョン `914f7f89142e33e77833254d9c9b90c3cef7303b`、sentence-transformers 6.1.0 / transformers 5.19.0 / torch 2.14.1
- プレフィックスはモデルカード準拠（クエリ `task: search result | query: `、文書 `title: none | text: `、分類 `task: classification | query: `）
- 次元は Matryoshka で切り詰めて再正規化

## タスク

| タスク | 内容 | 規模 |
|---|---|---|
| 記憶検索 | プレイヤーの発話から、該当する設定（known/unknown_facts・memories・relationships）1件を引く。正解は手動ラベル（`scenarios/embedding_retrieval.yaml`） | 35クエリ、コーパス63件 |
| └ 全キャラ横断 | 7人分すべてをコーパスにして検索 | |
| └ キャラ内 | 質問されたNPC自身の設定だけを検索 | |
| インジェクション検出 | 既存シナリオの発話を leave-one-out の kNN スコアで判別し、AUC を出す | 71発話中8件がインジェクション |
| 嘘の前提検出 | 同上（`false_premise` チェックのある発話） | 71発話中12件 |

## 結果（フルモデル、768次元）

| 指標 | 値 |
|---|---|
| 記憶検索（キャラ内） MRR / R@1 / R@3 | 0.948 / 0.914 / 0.971 |
| 記憶検索（全キャラ横断） MRR / R@1 / R@3 | 0.794 / 0.686 / 0.886 |
| 横断検索で1位が質問されたNPCの設定だった割合 | 71% |
| 「知らない事柄」(unknown_facts) を引けた割合（キャラ内R@1、4件） | 75% |
| インジェクション検出 AUC | 0.938 |
| 嘘の前提検出 AUC | 0.860 |

### Matryoshka 次元別

| 次元 | ベクトルサイズ(fp16) | キャラ内 MRR | 横断 MRR | 横断 R@1 | 横断で自NPC | インジェクション AUC | 嘘の前提 AUC |
|---|---|---|---|---|---|---|---|
| 768 | 1536 B | 0.948 | 0.794 | 0.686 | 71% | 0.938 | 0.860 |
| 512 | 1024 B | 0.960 | 0.793 | 0.686 | 71% | 0.933 | 0.846 |
| 256 | 512 B | 0.917 | 0.724 | 0.571 | 63% | 0.917 | 0.843 |
| 128 | 256 B | 0.893 | 0.686 | 0.543 | 63% | 0.901 | 0.826 |

### 速度とメモリ（GB10、フル768次元）

| 構成 | 重み | ピーク | 単発クエリ p50 / p95 | 長文書(約1,000字) p50 | 文書/秒 (バッチ32) |
|---|---|---|---|---|---|
| フルモデル（テキスト+画像+音声） | 1.50 GB | 2.08 GB | 17.3 / 17.5 ms | 22.4 ms | 1,379 |
| テキストのみ (`--text-only`) | 0.54 GB | 1.12 GB | 14.7 / 15.1 ms | 19.7 ms | 1,391 |

テキストのみ構成は、全次元で検索・検出の値がフルモデルと同一でした。

## 所見

- **キャラ内検索は実用的**（R@1 0.91、R@3 0.97）。NPC1人分の設定を引く用途なら、512次元（1KB/件）で落ちない（MRR 0.960）。
- **全キャラ横断はまだ弱い**。1位が別キャラの設定になる例が29%あり、横断で使うならキャラIDでの絞り込みが必要。
- **インジェクション・嘘の前提の検出は、この規模では参考値**。AUC は0.94/0.86だが、正例が8件・12件しかなく、kNN の単純な判別器での値。専用の分類器の代替にはならない。
- **256次元以下は検索が目に見えて落ちる**（横断R@1 0.686→0.571）。128次元は避ける。
- 日本語ゲーム用途のゲームNPC向けに埋め込みを選ぶなら、テキストのみ構成で十分（重み0.54GB、同一精度）。

## 注意

- 手動ラベルは35クエリと小さく、1件の差が約3ポイントです。次元間や構成間の小差は誤差の範囲です。
- 比較対象の埋め込みモデルは入れていません。`google/embeddinggemma-300m` はゲート付きで、この環境では認証がなく取得できませんでした。このため「他より良い／悪い」とは言えません。
- 速度はGB10（統一メモリ）の値で、4090とは比較できません。
- `tobestyledintro/qwen3.8-9b-distill` は生成モデルなので上の本編に含まれています（11位、NPC Score 82.1）。
