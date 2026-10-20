# ytc-movie-maker

ローカルLLM・ローカルTTS・Remotion レンダリングエンジンをマイクロサービスとして連携させ、
長尺解説動画（10分尺想定）を自動生成するシステム。

## アーキテクチャ

```
┌──────────────┐   ┌──────────────┐   ┌──────────────────┐   ┌───────────────┐
│  llm         │   │  tts         │   │ remotion-renderer│   │ orchestrator  │
│  (Ollama)    │   │  (VOICEVOX)  │   │ Node+Chromium+   │   │ (Python)      │
│  :11434      │   │  :50021      │   │ FFmpeg :3100     │   │               │
└──────▲───────┘   └──────▲───────┘   └────────▲─────────┘   └───────┬───────┘
       │ 1.台本生成        │ 2.音声合成         │ 3.セグメント描画       │ 統括
       │ (2段階JSON)       │ (WAV+時間)         │ 4.ffmpeg concat       │
       └──────────────────┴──────────────────┴───────────────────────┘
                         共有ボリューム ./shared-data
```

### OOM / タイムアウト対策（設計の要点）

- 台本は **チャプター（5〜8個）に分割** して生成・処理する。
- Remotion は **チャプターごとに個別 MP4（`seg_XX.mp4`）をレンダリング**。
  `renderMedia` 呼び出しごとに Chromium が起動・終了するため、18,000 フレーム分を
  一括でメモリに保持せず OOM を回避できる。
- 最後に **FFmpeg concat demuxer（`-c copy`、再エンコードなし）** で結合。
  全セグメントは同一コーデック設定（h264 / yuv420p / 30fps / 1920x1080）で描画される。
- LLM の台本生成も **2段階**（①全体目次 → ②チャプター別詳細セリフ）に分け、
  1回の `num_predict` 上限を超えないようにする。

## 前提

- Docker + Docker Compose v2
- 初回起動で以下をダウンロード:
  - Ollama イメージ + モデル（`llama3.2:3b` ≒ 2GB）
  - VOICEVOX Engine イメージ（CPU版 ≒ 2〜3GB）
  - remotion-renderer ビルド（npm install + ヘッドレスブラウザ ≒ 数百MB）

## セットアップ

```bash
cp .env.example .env        # 必要なら編集（モデル・話者ID等）
docker compose up -d --build
```

起動確認:

```bash
docker compose ps            # 全サービスが healthy になるまで待つ
docker compose logs -f llm-init   # モデル pull の進行確認（初回のみ数分）
```

`llm-init` は `llm` が healthy になった後にモデルを自動 pull して終了する。

## 動作テスト（モックモード）

`.env` の `MOCK_PIPELINE=true`（既定値）なら LLM を呼ばず、組み込みモック台本で
TTS → セグメントレンダリング → concat までの全経路を検証できる
（6章×6文 ≒ 2〜3分の動画）。

```bash
docker compose exec orchestrator python main.py --topic "大規模言語モデルの仕組み" --mock
```

成功すると `shared-data/jobs/<job-id>/final.mp4` が出力される。

```bash
ls shared-data/jobs/*/          # script.json, manifest.json, audio/, renders/, final.mp4
ffprobe shared-data/jobs/<job-id>/final.mp4   # 尺・コーデック確認
```

## 本番パイプライン（実LLM・10分尺）

```bash
docker compose exec orchestrator python main.py --topic "相対性理論をやさしく解説" --no-mock
```

処理フロー（orchestrator のログで進行確認可能）:

1. `[LLM 1/2]` 全体タイトル＋チャプター構成を JSON で生成（`format: json` で出力保証）
2. `[LLM 2/2]` チャプターごとに narration（文配列）・bullets・image_prompt を生成
3. `[2/4]` 各文を VOICEVOX で WAV 化し、発話時間を取得してタイミング表を構築
4. `[3/4]` `POST /render-segment` でチャプター別 MP4 を逐次レンダリング
5. `[4/4]` `POST /concat` で `final.mp4` に結合

10分に届かない場合は `CHAPTER_COUNT` を増やすか、`TARGET_DURATION_MIN` を上げると
1章あたりの生成文数が自動で増える。

## 画面構成（Remotion Composition）

- 1920x1080 / 30fps
- 上部: 動画全体に対する進行度バー（セグメント横断で連続）
- 左上: `CHAPTER n / N` + チャプター見出し（spring アニメーション）
- 中央: 箇条書き要点スライド（順次フェードイン）
- 下部: ナレーション字幕（発話タイミングに同期して切替）
- `<Audio>` でセリフ WAV を `<Sequence>` 配置し時間通りに再生

## 各サービスの API

| サービス | エンドポイント | 用途 |
|---|---|---|
| llm | `POST :11434/api/generate` | Ollama テキスト生成 (`format:"json"`) |
| tts | `POST :50021/audio_query`, `POST :50021/synthesis` | VOICEVOX クエリ作成・音声合成 |
| remotion-renderer | `GET :3100/health` | ヘルスチェック |
| remotion-renderer | `POST :3100/render-segment` | `{manifest, segmentIndex}` → セグメントMP4 |
| remotion-renderer | `POST :3100/concat` | `{dir, files, output}` → 結合MP4 |
| remotion-renderer | `GET :3100/shared/*` | shared-data の静的配信（Audio 取得用） |

## 調整パラメータ（.env）

| 変数 | 既定 | 説明 |
|---|---|---|
| `OLLAMA_MODEL` | `llama3.2:3b` | 使用モデル。`mistral:7b` 等に変更可（llm-init が自動 pull） |
| `VOICEVOX_SPEAKER` | `3` | 話者ID。`./services/tts/list-speakers.sh` で一覧確認 |
| `CHAPTER_COUNT` | `6` | チャプター数（5〜8推奨） |
| `TARGET_DURATION_MIN` | `10` | 目標尺（分）。生成文数の目安に使用 |
| `RENDER_CONCURRENCY` | `2` | Remotion 並列レンダリング数。OOM 時は `1` |
| `SENTENCE_GAP_SEC` | `0.35` | セリフ間ポーズ |
| `MOCK_PIPELINE` | `true` | `false`、または実行時 `--no-mock` で実LLM使用 |
| `RUN_ON_STARTUP` | `false` | `true` でコンテナ起動時に自動ジョブ実行 |

## トラブルシューティング

- **Chromium が OOM で落ちる**: `.env` で `RENDER_CONCURRENCY=1`。
  それでも駄目なら `CHAPTER_COUNT` を増やして1セグメントのフレーム数を減らす。
- **LLM が JSON を返さない**: orchestrator は JSON 抽出＋各章1回リトライ済み。
  それでも失敗する場合は `OLLAMA_MODEL` を `mistral:7b` 等に変更。
- **concat で音ズレ/エラー**: 全セグメントは同一設定で描画済みだが、
  万一 `-c copy` が失敗する場合は render-server の concat を
  `-c:v libx264 -c:a aac` の再エンコードに変更する。
- **TTS が遅い**: CPU版 VOICEVOX は文数が多いと時間がかかる。
  GPU 版は `.env` の `VOICEVOX_IMAGE` を `voicevox/voicevox_engine:nvidia-ubuntu20.04-latest` に。

## ディレクトリ構成

```
docker-compose.yml
.env.example
shared-data/jobs/<job-id>/
  script.json        # LLM生成台本
  manifest.json      # レンダリング用タイミング表
  audio/ch*/s*.wav   # TTS 音声
  renders/seg_*.mp4  # セグメントMP4
  final.mp4          # 最終成果物
services/
  orchestrator/      # main.py: パイプライン統括
  remotion/          # Remotion プロジェクト + render-server.ts
  llm/pull-model.sh  # モデル手動pull
  tts/list-speakers.sh
```
