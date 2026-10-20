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
5. `[4/4]` `POST /concat` で結合 → `POST /finalize` で **BGMダッキング＋
   ラウドネス正規化(-14 LUFS)** を適用して `final.mp4` を出力。
   同時に YouTube 概要欄用 `description.txt`（章タイムスタンプ＋BGMクレジット）を生成

## BGM（DOVA-SYNDROME / 効果音ラボ）

公式APIがないため、楽曲は手動DLしてローカルライブラリに登録する設計
（登録手順: `services/bgm/README.md` 参照）。

- `shared-data/bgm/` に mp3 を配置し `library.json` に mood・作曲者・
  ライセンス・URL を登録
- LLM が台本生成時に `bgm_mood`（calm/epic/upbeat等）を返す → mood 一致の
  トラックをランダム選定（ライブラリ未登録なら BGMなしで続行）
- ミックス: BGMループ + `sidechaincompress` でナレーションに合わせ自動ダッキング
  + `loudnorm` で YouTube 基準 -14 LUFS に正規化（`BGM_VOLUME` で基準音量調整）
- 選んだ曲のクレジットが `description.txt` に自動転記される

## 広告はく奪・収益化審査リスク低減の実装

| リスク | 組み込み対策 |
|---|---|
| BGM著作権・クレジット漏れ | library.json でライセンス情報を必須管理し description.txt に自動転記 |
| Content ID 一致 | 楽曲選定ガイドラインを `services/bgm/README.md` に明記（「著作権フリー」表記の曲を使用） |
| 「繰り返しの多いコンテンツ」判定 | ジョブID由来のアクセント色で見た目に差分／BGMを mood タグで分散 |
| 音声品質が低い判定 | ナレーション明瞭化のダッキング＋-14 LUFS ラウドネス正規化 |
| 概要欄の薄さ | 章タイムスタンプ・クレジット自動生成（YouTubeチャプターにも有効） |

運用面の補足: 台本は LLM 生成のオリジナル解説だが、同一テンプレ動画の大量投稿は
「再利用コンテンツ」と判定され得る。トピック・章構成のバリエーションを持たせ、
収益化審査前に数本手動で品質確認することを推奨。

## キャラクター立ち絵

`shared-data/characters/` に立ち絵PNGを配置すると、動画右下に表示される
（スライド・字幕は自動で左寄せに調整）。

- `CHARACTER_IMAGES` にカンマ区切りで指定（既定: `characters/tsumugi-1.png`）。
  2枚目を追加するとウインク差分として約4秒周期で切替（まばたき演出）
- 春日部つむぎの公式立ち絵は **公式サイトの利用規約に従って入手** すること。
  同梱の readme.txt に従い、立ち絵利用動画は商用利用可・二次配布は禁止
- 立ち絵使用時は `description.txt` にクレジットを自動付記
  （`CHARACTER_CREDIT`、既定: `VOICEVOX:春日部つむぎ / 立ち絵=春日部つくし`）

## 読み上げ精度（漢字の誤読対策）

2段構えで TTS の誤読を抑止:

1. **LLM 注記**: 台本生成時に難読語へ `漢字(よみ)` 形式の読み仮名を要求。
   orchestrator が `漢字(よみ)` → TTS には `よみ`（かな入力は確実に読まれる）、
   字幕には `漢字` を使い分ける
2. **矯正辞書**: `shared-data/tts/reading_fixes.json` に
   `{"誤読される語": "よみ"}` を登録すると全セリフに強制置換
   （製品名・モデル名など LLM 非依存で確実に潰せる）

## YouTube アップロード

`--upload` または `YT_UPLOAD=true` で、動画完成後に自動アップロードする。

- **非公開（`privacyStatus: private`）でアップロード**（`YT_PRIVACY` で変更可）
- タイトル/説明文/タグは LLM 生成メタデータ（`upload_metadata.json`）から適用
- サムネイルは完成動画の代表フレームを ffmpeg で JPEG 抽出して `thumbnails.set`
- `status.containsSyntheticMedia: true` を付与（AI生成コンテンツ申告）

### 初回セットアップ

1. Google Cloud Console で OAuth クライアントを作成し YouTube Data API v3 を有効化
2. リフレッシュトークンを取得（スコープ: `.../auth/youtube.upload`）:
   ```bash
   docker compose exec orchestrator \
     python get_refresh_token.py --client-id XXX --client-secret YYY
   # またはホストで直接: python3 services/orchestrator/get_refresh_token.py ...
   ```
   表示URLをブラウザで開き認可 → `localhost:8085` にリダイレクトされトークン表示
3. `.env` に `YT_CLIENT_ID` / `YT_CLIENT_SECRET` / `YT_REFRESH_TOKEN` を設定
4. アクセストークンはリフレッシュトークンから毎回自動更新されるため期限切れ不要

### 注意

- **未審査（unverified）のAPIプロジェクトからアップロードした動画は
  `private` に制限される**（YouTubeの監査ポリシー）。公開するには Studio で
  手動公開するか、APIプロジェクトの監査を通す必要がある
- 動画自体は private で投稿されるため、まず Studio で内容確認→公開の運用が安全

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
| remotion-renderer | `POST :3100/finalize` | `{dir, video, bgm?, output}` → BGMミックス＋正規化 |
| remotion-renderer | `POST :3100/thumbnail` | `{dir, video, timeSec?, output}` → 代表フレームJPEG |
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
| `VOICEVOX_SPEED` | `1.2` | ナレーション速度（VOICEVOX speedScale） |
| `BGM_MOOD` | `calm` | BGM フォールバックムード |
| `BGM_VOLUME` | `0.22` | BGM基準音量（ダッキング前） |
| `MOCK_PIPELINE` | `true` | `false`、または実行時 `--no-mock` で実LLM使用 |
| `RUN_ON_STARTUP` | `false` | `true` でコンテナ起動時に自動ジョブ実行 |
| `YT_UPLOAD` | `false` | `true` で完了後に YouTube 自動アップロード |
| `YT_PRIVACY` | `private` | アップロード時の公開設定 |
| `YT_CATEGORY_ID` | `27` | 動画カテゴリID |
| `YT_CLIENT_ID/SECRET/REFRESH_TOKEN` | — | OAuth2 認証情報 |

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
shared-data/
  bgm/library.json   # BGMライブラリ（mood/ライセンス管理）
  tts/reading_fixes.json  # TTS読み上げ矯正辞書
  jobs/<job-id>/
    script.json        # LLM生成台本
    manifest.json      # レンダリング用タイミング表
    audio/ch*/s*.wav   # TTS 音声
    renders/seg_*.mp4  # セグメントMP4
    concat.mp4         # 結合のみの中間成果物
    final.mp4          # 最終成果物（BGM＋正規化済み）
    description.txt    # YouTube概要欄用テキスト
services/
  orchestrator/      # main.py: パイプライン統括
  remotion/          # Remotion プロジェクト + render-server.ts
  llm/pull-model.sh  # モデル手動pull
  tts/list-speakers.sh
```
