# 検証結果（2026-10-07）

日本語ゲームNPC用途で、7B〜14Bクラスを中心にローカルLLM 11設定を比較した結果です。
詳細なレポート（グラフ・同じ会話への全モデルの返答の横並び比較）は [reports/all_models_v2/report.html](reports/all_models_v2/report.html)、
全試行の生データは [results/all_models_v2/rows.jsonl](results/all_models_v2/rows.jsonl) にあります。

## 条件

- GPU: NVIDIA GeForce RTX 4090 24GB（driver 590.48.01）。ComfyUI等の他プロセスと共有（計測時は約4GB使用）
- サンプリング: temperature 0.7 / top_p 0.9 / max_tokens 128、seed固定（全モデル共通）
- 品質: 全73テスト × 5回（1モデル605生成）。ルール評価は全試行、LLM Judge（gpt-oss:20b）は最初の2回分
- 負荷: 同時実行 1 / 2 / 4 / 8 / 16、各30〜48リクエスト（ウォームアップ後）
- Qwen3系は思考モードOFF。Qwen3-14B BF16は24GBに載らないため対象外
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
| 9 | Llama-3-ELYZA-JP-8B (BF16, vLLM) | 87.8 | 86.4 | 84.9 | 92.8 | 7.6% | 78.3 | 28 / 44 | 60 | 21.7 | 18.5 | **82.3** |
| 10 | Qwen3.8-9B-Distill (GGUF Q4_K_M, llama.cpp) | 77.7 | 65.8 | 81.4 | 78.4 | 7.6% | 66.7 | 138 / 221 | 137 | 4.2 | 8.1 | **82.1** |
| 11 | Shisa V2 Qwen2.5-7B (BF16, vLLM) | 81.5 | 72.3 | 85.1 | 77.2 | 6.9% | 77.7 | 25 / 27 | 63 | 14.5 | 18.5 | **78.6** |

## ランキング（上位3）

| 部門 | 1位 | 2位 | 3位 |
|---|---|---|---|
| Best Quality | Qwen3-14B (FP8, vLLM) | Llama-3-ELYZA-JP-8B (GGUF Q4_K_M, llama.cpp) | Qwen3-14B (GGUF Q4_K_M, llama.cpp) |
| Best Latency (TTFT p50) | Qwen3-8B (AWQ, vLLM) | Qwen3-14B (AWQ, vLLM) | Llama-3-ELYZA-JP-8B (GGUF Q4_K_M, llama.cpp) |
| Best Japanese | Qwen3-14B (FP8, vLLM) | Qwen3-14B (GGUF Q4_K_M, llama.cpp) | Qwen3-8B (AWQ, vLLM) |
| Best Character Consistency | Qwen3-14B (FP8, vLLM) | Qwen3-14B (GGUF Q4_K_M, llama.cpp) | Qwen3-14B (AWQ, vLLM) |
| Lowest Hallucination | Qwen3-14B (GGUF Q4_K_M, llama.cpp) | Qwen3-14B (FP8, vLLM) | Qwen3-8B (Q4_K_M, Ollama) |
| Best VRAM Efficiency (quality / GB) | Gemma3-4B-it (Q4_K_M, Ollama) | Qwen2.5-7B-Instruct (Q4_K_M, Ollama) | Qwen3-8B (Q4_K_M, Ollama) |
| Best Overall NPC Model | Qwen3-8B (Q4_K_M, Ollama) | Llama-3-ELYZA-JP-8B (GGUF Q4_K_M, llama.cpp) | Qwen3-14B (AWQ, vLLM) |

## 所見

- **品質はQwen3-14B系が最上位**（FP8 90.8、日本語・キャラ一貫性も1位）。ハルシネーション最少は Qwen3-14B GGUF（1.4%）。ただし上位5モデルの品質差（89.6〜90.8）は5回の実行間のぶれ（±2〜3点）より小さく、順位は統計的に確定しない。
- **速度・同時接続は vLLM が圧倒的**。Qwen3-8B AWQ は16同時でも TTFT p95 50ms・46 req/s。Ollama は同モデルでも約5 req/sで頭打ち（`OLLAMA_NUM_PARALLEL` 依存）。
- **日本語特化モデル**: ELYZA-JP-8B は知識整合性が高いが嘘の前提への同意がやや多い（7.6%）。Shisa V2 7B は返答が長すぎる（37%）、ト書き混入、コードを書く誘導に従う。
- **Qwen2.5-7B** は返答の約11%に英単語が混入（「undergroundのことか？」）。
- **Qwen3.8-9B-Distill**（Ollama `tobestyledintro/qwen3.8-9b-distill`、元ファイル名 `Qwen3.8-9B-Distill-uncensored-heretic`）は品質最下位（77.5）。55%が長すぎ・複数行の「」書き・ト書き。TTFTも llama.cpp 上で他モデルより遅い（p50 138ms）。

## 注意

- **VRAMはサーバーの確保量**。vLLMは `--gpu-memory-utilization` 分、llama.cppは `-c` 分のKVキャッシュを含む。このためvLLMのFP8/BF16モデルはメモリ効率で不利になりNPC Scoreが下がっている（重みは `config.yaml: npc_score_weights` で変更可）。
- Qwen3-14B FP8 は空きVRAMの都合で `--max-model-len 4096`（テストの最長プロンプトは約2,800トークン）、また古いnvccでFlashInferのJITが通らないため `--attention-backend TRITON_ATTN`。
- ルールベース評価は正規表現。検証中に見つかった誤検出（日本語と共通の漢字を簡体字扱い、JSONキーや作中固有名詞の英字、「〜てない」形の否定、オウム返し）を修正し、全行を `--rescore` で同じルールで再採点済み。
- Judge（gpt-oss:20b）は全体に甘め（明確なでっち上げにも高得点をつける例あり）。
