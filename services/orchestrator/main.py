#!/usr/bin/env python3
"""
Orchestrator: LLM(2段階) → TTS → Remotion(セグメント別) → FFmpeg concat
長尺動画パイプラインを統括するワーカー。

使い方:
  python main.py --topic "LLMの仕組み"          # 本番パイプライン
  python main.py --mock                        # LLMをモック化して疎通検証
  python main.py --run-mode env                # RUN_ON_STARTUP に従い実行/待機
"""
import argparse
import hashlib
import io
import json
import os
import random
import re
import sys
import time
import uuid
import wave
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------
# 設定
# ---------------------------------------------------------------
def env(name: str, default: str) -> str:
    return os.environ.get(name, default)

OLLAMA_BASE_URL = env("OLLAMA_BASE_URL", "http://llm:11434").rstrip("/")
OLLAMA_MODEL = env("OLLAMA_MODEL", "llama3.2:3b")
VOICEVOX_BASE_URL = env("VOICEVOX_BASE_URL", "http://tts:50021").rstrip("/")
VOICEVOX_SPEAKER = int(env("VOICEVOX_SPEAKER", "3"))
VOICEVOX_SPEED = float(env("VOICEVOX_SPEED", "1.2"))
RENDERER_URL = env("RENDERER_URL", "http://remotion-renderer:3100").rstrip("/")
SHARED_DIR = env("SHARED_DIR", "/shared-data")
FPS = int(env("VIDEO_FPS", "30"))
WIDTH = int(env("VIDEO_WIDTH", "1920"))
HEIGHT = int(env("VIDEO_HEIGHT", "1080"))
TARGET_MIN = float(env("TARGET_DURATION_MIN", "10"))
CHAPTER_COUNT = int(env("CHAPTER_COUNT", "6"))
GAP_SEC = float(env("SENTENCE_GAP_SEC", "0.35"))
BGM_MOOD = env("BGM_MOOD", "calm")  # フォールバック用ムード
# 立ち絵（shared-data からの相対パスをカンマ区切りで複数可。
# 1枚目=基本、2枚目=ウインク差分として周期的に切り替わる）
CHARACTER_IMAGES = env(
    "CHARACTER_IMAGES",
    env("CHARACTER_IMAGE", "characters/tsumugi-1.png,characters/tsumugi-2.png"))
CHARACTER_CREDIT = env(
    "CHARACTER_CREDIT", "VOICEVOX:春日部つむぎ / 立ち絵=春日部つくし")
TAIL_PAD_SEC = 0.8  # セグメント末尾の余白

HTTP = requests.Session()
HTTP_TIMEOUT = (10, 600)  # (connect, read) 秒 — LLM生成・レンダリングは長い


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------
# ステップ1: LLM — 2段階呼び出し
# ---------------------------------------------------------------
def ollama_generate(prompt: str, num_predict: int = 2048) -> str:
    """Ollama /api/generate を JSON モードで呼ぶ。"""
    resp = HTTP.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"num_predict": num_predict, "temperature": 0.7},
        },
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["response"]


def extract_json(text: str) -> dict:
    """LLM出力から最初のJSONオブジェクトを頑健に抽出する。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f"JSONを抽出できません: {text[:300]}")


def stage1_outline(topic: str) -> dict:
    """第1段階: 動画タイトルとチャプター構成だけを生成させる。"""
    prompt = f"""あなたはYouTube解説動画の構成作家です。
テーマ「{topic}」について、約{TARGET_MIN:.0f}分の解説動画の構成を作成してください。

出力は必ず以下のJSON形式のみ。余計な文章は一切出力しないこと:
{{
  "title": "動画タイトル",
  "bgm_mood": "動画の雰囲気を表す英単語1つ (例: calm, epic, upbeat, dark, relaxing)",
  "tags": ["YouTube投稿用タグ1", "タグ2"],
  "chapters": [
    {{"id": "ch1", "title": "チャプター見出し", "brief": "この章で説明すること(1文)"}}
  ]
}}
チャプターはちょうど{CHAPTER_COUNT}個。idは ch1, ch2, ... とすること。
tagsは検索に有効な関連キーワード5〜10個。"""
    log(f"[LLM:1/2] 目次生成中... (model={OLLAMA_MODEL})")
    data = extract_json(ollama_generate(prompt, num_predict=1024))
    chapters = data["chapters"][:CHAPTER_COUNT]
    for i, ch in enumerate(chapters, 1):
        ch["id"] = f"ch{i}"
    data["chapters"] = chapters
    log(f"[LLM:1/2] {len(chapters)}チャプター: " +
        ", ".join(c["title"] for c in chapters))
    return data


def stage2_chapter(topic: str, video_title: str, chapter: dict) -> dict:
    """第2段階: 1チャプター分のセリフ・箇条書きを詳細生成する。"""
    chapter_min = TARGET_MIN / CHAPTER_COUNT
    n_sent = max(4, round(chapter_min * 9))  # 1文≒7秒想定
    prompt = f"""あなたはYouTube解説動画の脚本家です。
動画「{video_title}」(テーマ: {topic}) のチャプター「{chapter['title']}」の台本を書いてください。
このチャプターの内容: {chapter.get('brief','')}

出力は必ず以下のJSON形式のみ:
{{
  "title": "{chapter['title']}",
  "bullets": ["要点1", "要点2", "要点3"],
  "narration": ["ナレーション文1。", "ナレーション文2。"],
  "image_prompt": "この章の内容を表す背景イラストの英語プロンプト"
}}
narration は1文ずつの配列で、合計{n_sent}文程度(約{chapter_min:.1f}分の話量)。
bullets は3〜5個の短い箇条書き。
重要: narration では読み間違いやすい漢字・難読語・固有名詞・数字に必ず
半角括弧で読み仮名(ひらがな)を付けること。例: 解釈(かいしゃく)、
営業(えいぎょう)利益(りえき)、東京都(とうきょうと)。
読みが自明な語には付けなくてよい。"""
    log(f"[LLM:2/2] チャプター「{chapter['title']}」の台本生成中...")
    data = extract_json(ollama_generate(prompt, num_predict=3000))
    data.setdefault("title", chapter["title"])
    data.setdefault("image_prompt", "")
    if not isinstance(data.get("bullets"), list):
        data["bullets"] = []
    if not isinstance(data.get("narration"), list) or not data["narration"]:
        raise ValueError(f"チャプター{chapter['id']}: narration が空です")
    data["id"] = chapter["id"]
    log(f"[LLM:2/2] {chapter['id']}: {len(data['narration'])}文, "
        f"{len(data['bullets'])}要点")
    return data


def build_mock_script(topic: str) -> dict:
    """LLMを呼ばずに疎通検証用スクリプトを生成（短尺・本数少なめ）。"""
    n_sent = int(env("MOCK_SENTENCES_PER_CHAPTER", "6"))
    chapters = []
    for i in range(1, CHAPTER_COUNT + 1):
        chapters.append({
            "id": f"ch{i}",
            "title": f"チャプター{i}: {topic}のポイント{i}",
            "bullets": [f"ポイント{i}-{j}" for j in range(1, 4)],
            "narration": [
                f"これは{topic}に関するチャプター{i}のテストナレーション、{j}文目です。"
                if j > 1
                else f"これは読み仮名(かな)注記の検証文です。解釈(かいしゃく)を確認します。"
                for j in range(1, n_sent + 1)
            ],
            "image_prompt": "",
        })
    log(f"[MOCK] {CHAPTER_COUNT}チャプター×{n_sent}文の台本を生成")
    return {
        "title": f"【テスト】{topic}",
        "bgm_mood": BGM_MOOD,
        "tags": [topic, "解説", "自動生成"],
        "chapters": chapters,
    }


# ---------------------------------------------------------------
# 読み上げ精度: 注記パース + 矯正辞書
# LLM は難読語に `漢字(よみ)` 形式の注記を付ける。
#   字幕表示用: 漢字(よみ) → 漢字
#   TTS入力用 : 漢字(よみ) → よみ  （かな入力は確実に読まれる）
# reading_fixes.json で LLM非依存の強制置換も可能。
# ---------------------------------------------------------------
_READING_FIXES_PATH = os.path.join(SHARED_DIR, "tts", "reading_fixes.json")
_KANA_PAREN = r"([ぁ-んァ-ヶー]+)"  # 読み仮名注記は括弧内がかなのみ
# 語の先頭は漢字/カタカナ/英数（直前の助詞を食わないようひらがなは除外）
_ANNOTATE_RE = re.compile(
    r"[一-龯々ァ-ヶa-zA-Z0-9][一-龯々ぁ-んァ-ヶa-zA-Z0-9]*\(" + _KANA_PAREN + r"\)")
_PAREN_RE = re.compile(r"\(" + _KANA_PAREN + r"\)")
_reading_fixes: dict | None = None


def annotated_display(text: str) -> str:
    return _PAREN_RE.sub("", text)


def annotated_speech(text: str) -> str:
    return _ANNOTATE_RE.sub(lambda m: m.group(1), text)


def load_reading_fixes() -> dict:
    global _reading_fixes
    if _reading_fixes is None:
        _reading_fixes = {}
        if os.path.exists(_READING_FIXES_PATH):
            try:
                data = json.load(open(_READING_FIXES_PATH, encoding="utf-8"))
                items = data.items() if isinstance(data, dict) else \
                    ((d["word"], d["yomi"]) for d in data)
                _reading_fixes = {w: r for w, r in items
                                  if not str(w).startswith("_")}
                log(f"[読み矯正] {len(_reading_fixes)}語を読み込み")
            except Exception as e:
                log(f"[読み矯正] reading_fixes.json 読み込み失敗: {e}")
    return _reading_fixes


def apply_fixes(text: str) -> str:
    for w, r in load_reading_fixes().items():
        text = text.replace(w, r)
    return text


# ---------------------------------------------------------------
# ステップ2: TTS (VOICEVOX)
# ---------------------------------------------------------------
def synthesize_wav(text: str) -> bytes:
    """audio_query → synthesis でWAVバイト列を取得。"""
    q = HTTP.post(
        f"{VOICEVOX_BASE_URL}/audio_query",
        params={"text": text, "speaker": VOICEVOX_SPEAKER},
        timeout=HTTP_TIMEOUT,
    )
    q.raise_for_status()
    query = q.json()
    query["speedScale"] = VOICEVOX_SPEED  # ナレーション速度
    s = HTTP.post(
        f"{VOICEVOX_BASE_URL}/synthesis",
        params={"speaker": VOICEVOX_SPEAKER},
        json=query,
        timeout=HTTP_TIMEOUT,
    )
    s.raise_for_status()
    return s.content


def wav_duration_sec(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.getnframes() / w.getframerate()


def tts_chapter(chapter: dict, audio_dir: str, rel_dir: str) -> list:
    """1チャプター分の全セリフを音声化し、タイミング付き配列を返す。"""
    sentences = []
    cursor = 0.0
    for j, raw in enumerate(chapter["narration"]):
        display = annotated_display(raw)          # 字幕: 漢字表記のまま
        speech = apply_fixes(annotated_speech(raw))  # 音声: かな読み+矯正
        wav = synthesize_wav(speech)
        dur = wav_duration_sec(wav)
        fname = f"s{j:02d}.wav"
        with open(os.path.join(audio_dir, fname), "wb") as f:
            f.write(wav)
        sentences.append({
            "text": display,
            "audio": f"{rel_dir}/{fname}",
            "startSec": round(cursor, 3),
            "durationSec": round(dur, 3),
        })
        cursor += dur + GAP_SEC
    return sentences


# ---------------------------------------------------------------
# BGM ライブラリ（shared-data/bgm/library.json）
# DOVA-SYNDROME / 効果音ラボ 等からダウンロードした楽曲を
# ムード・ライセンス情報付きで登録しておく想定。
# ---------------------------------------------------------------
def select_bgm(mood: str) -> dict | None:
    lib_path = os.path.join(SHARED_DIR, "bgm", "library.json")
    if not os.path.exists(lib_path):
        log("[BGM] library.json なし → BGMなしで続行")
        return None
    try:
        tracks = json.load(open(lib_path, encoding="utf-8")).get("tracks", [])
    except Exception as e:
        log(f"[BGM] library.json 読み込み失敗: {e} → BGMなしで続行")
        return None
    if not tracks:
        log("[BGM] 登録トラックなし → BGMなしで続行")
        return None
    mood_l = (mood or "").lower()
    matched = [t for t in tracks
               if mood_l and mood_l in [m.lower() for m in t.get("mood", [])]]
    track = random.choice(matched or tracks)
    mp3 = os.path.join(SHARED_DIR, "bgm", track["file"])
    if not os.path.exists(mp3):
        log(f"[BGM] {track['file']} が見つからない → BGMなしで続行")
        return None
    log(f"[BGM] mood={mood!r} → 「{track.get('title','?')}」"
        f"({track.get('artist','?')}) {'mood一致' if matched else 'fallback'}")
    return track


def write_description(job_dir: str, script: dict, segments: list,
                      total_sec: float, bgm: dict | None) -> str:
    """YouTube投稿用 description.txt（章タイムスタンプ＋BGMクレジット）。
    生成した本文を返す。"""
    lines = [script["title"], "", "■ チャプター"]
    for seg in segments:
        t = int(seg["progressStart"] * total_sec)
        lines.append(f"{t//60:02d}:{t%60:02d} {seg['title']}")
    lines.append("")
    if bgm:
        lines += [
            "■ BGM",
            f"「{bgm.get('title','')}」 {bgm.get('artist','')}",
            f"提供: {bgm.get('source','')}",
            f"URL: {bgm.get('url','')}",
            f"ライセンス: {bgm.get('license','')}",
            "",
        ]
    lines += [
        "■ ナレーション",
        "VOICEVOX（合成音声）",
    ]
    if script.get("_character_used"):
        lines.append(f"キャラクター: {CHARACTER_CREDIT}")
    lines += [
        "",
        "■ この動画について",
        "本動画はAIによる合成コンテンツです。",
        "台本: ローカルLLM自動生成 / 音声: VOICEVOX合成音声 / 映像: 自動生成スライド",
    ]
    text = "\n".join(lines) + "\n"
    with open(os.path.join(job_dir, "description.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    return text


def write_upload_metadata(job_dir: str, script: dict, description: str) -> None:
    """YouTube Data API videos.insert 用メタ情報。
    privacyStatus は既定 private（非公開アップロード）。
    containsSyntheticMedia は YouTube Studio「改変コンテンツ」→ はい、に相当。"""
    meta = {
        "title": script["title"][:100],
        "description": description,
        "tags": script.get("tags", [])[:15],
        "privacyStatus": env("YT_PRIVACY", "private"),
        "categoryId": env("YT_CATEGORY_ID", "27"),  # 27=Education
        "thumbnail": "thumbnail.jpg",
        "containsSyntheticMedia": True,
        "syntheticMediaNote": "LLM生成台本 + VOICEVOX合成音声 + 自動生成映像",
        "madeForKids": False,
    }
    with open(os.path.join(job_dir, "upload_metadata.json"), "w",
              encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------
# ステップ3: Remotion レンダリング & FFmpeg concat
# ---------------------------------------------------------------
def render_segment(manifest_path_rel: str, index: int) -> str:
    resp = HTTP.post(
        f"{RENDERER_URL}/render-segment",
        json={"manifest": manifest_path_rel, "segmentIndex": index},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["file"]


def concat_segments(job_rel_dir: str, files: list, out_name: str) -> str:
    resp = HTTP.post(
        f"{RENDERER_URL}/concat",
        json={"dir": job_rel_dir, "files": files, "output": out_name},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["file"]


def finalize_video(job_rel_dir: str, video: str, bgm_file: str | None) -> str:
    """BGMダッキング＋ラウドネス正規化を適用して final.mp4 を出力。"""
    resp = HTTP.post(
        f"{RENDERER_URL}/finalize",
        json={"dir": job_rel_dir, "video": video,
              "bgm": bgm_file, "output": "final.mp4"},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["file"]


def make_thumbnail(job_rel_dir: str, time_sec: float) -> str:
    """完成動画の代表フレームを JPEG で抽出。"""
    resp = HTTP.post(
        f"{RENDERER_URL}/thumbnail",
        json={"dir": job_rel_dir, "video": "final.mp4",
              "timeSec": round(time_sec, 1), "output": "thumbnail.jpg"},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["file"]


# ---------------------------------------------------------------
# パイプライン本体
# ---------------------------------------------------------------
def run_pipeline(topic: str, mock: bool, job_id: str | None = None,
                 upload: bool = False) -> str:
    job_id = job_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + \
        "-" + uuid.uuid4().hex[:6]
    job_rel = f"jobs/{job_id}"
    job_dir = os.path.join(SHARED_DIR, job_rel)
    os.makedirs(job_dir, exist_ok=True)
    log(f"=== ジョブ開始: {job_id} topic={topic!r} mock={mock} ===")

    # --- 1. 台本生成 ---
    t0 = time.time()
    if mock:
        script = build_mock_script(topic)
    else:
        script = stage1_outline(topic)
        detailed = []
        for ch in script["chapters"]:
            for attempt in (1, 2):
                try:
                    detailed.append(stage2_chapter(topic, script["title"], ch))
                    break
                except Exception as e:
                    if attempt == 2:
                        raise
                    log(f"[LLM:2/2] {ch['id']} リトライ: {e}")
        script["chapters"] = detailed
    with open(os.path.join(job_dir, "script.json"), "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)
    log(f"[1/4] 台本完成 ({time.time()-t0:.0f}s) -> {job_rel}/script.json")

    # --- 2. TTS ---
    t0 = time.time()
    segments = []
    total_sec = 0.0
    chapter_sents = []
    for i, ch in enumerate(script["chapters"]):
        audio_dir = os.path.join(job_dir, "audio", ch["id"])
        os.makedirs(audio_dir, exist_ok=True)
        sents = tts_chapter(ch, audio_dir, f"{job_rel}/audio/{ch['id']}")
        chapter_sents.append(sents)
        dur = sum(s["durationSec"] for s in sents) + GAP_SEC * len(sents)
        total_sec += dur + TAIL_PAD_SEC
        log(f"[2/4] TTS {ch['id']}: {len(sents)}文 {dur:.1f}s")

    cursor_frac = 0.0
    for i, (ch, sents) in enumerate(zip(script["chapters"], chapter_sents)):
        dur = sents[-1]["startSec"] + sents[-1]["durationSec"] + TAIL_PAD_SEC
        frac = dur / total_sec
        segments.append({
            "index": i,
            "id": ch["id"],
            "title": ch["title"],
            "bullets": ch["bullets"],
            "chapterNumber": i + 1,
            "chapterCount": len(script["chapters"]),
            "sentences": sents,
            "progressStart": round(cursor_frac, 6),
            "progressEnd": round(cursor_frac + frac, 6),
            "durationInFrames": int(dur * FPS + 0.999),
        })
        cursor_frac += frac
    segments[-1]["progressEnd"] = 1.0

    # ジョブ固有のアクセント色（テンプレ感抑制・動画ごとのバリエーション）
    accent_hue = int(hashlib.md5(job_id.encode()).hexdigest(), 16) % 360

    # 立ち絵（カンマ区切り複数指定可。配置されているもののみ使用）
    char_paths = [p.strip() for p in CHARACTER_IMAGES.split(",") if p.strip()]
    char_paths = [p for p in char_paths
                  if os.path.exists(os.path.join(SHARED_DIR, p))]
    has_char = bool(char_paths)
    script["_character_used"] = has_char
    if has_char:
        log(f"[立ち絵] {', '.join(char_paths)} を使用")

    manifest = {
        "jobId": job_id,
        "title": script["title"],
        "fps": FPS, "width": WIDTH, "height": HEIGHT,
        "gapSec": GAP_SEC,
        "accentHue": accent_hue,
        "characterImage": char_paths[0] if has_char else None,
        "characterWink": char_paths[1] if len(char_paths) > 1 else None,
        "totalDurationSec": round(total_sec, 2),
        "segments": segments,
    }
    manifest_rel = f"{job_rel}/manifest.json"
    with open(os.path.join(job_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    log(f"[2/4] TTS完了 ({time.time()-t0:.0f}s) 合計 {total_sec/60:.1f}分 -> {manifest_rel}")

    # --- 3. セグメント別レンダリング（OOM対策: 逐次・個別プロセス） ---
    t0 = time.time()
    seg_files = []
    for seg in segments:
        log(f"[3/4] レンダリング seg{seg['index']} "
            f"({seg['durationInFrames']}f / {seg['durationInFrames']/FPS:.0f}s)")
        out = render_segment(manifest_rel, seg["index"])
        seg_files.append(out)
        log(f"[3/4] seg{seg['index']} 完了 -> {out}")
    log(f"[3/4] 全{len(seg_files)}セグメント描画完了 ({time.time()-t0:.0f}s)")

    # --- 4. FFmpeg concat → BGMミックス＋ラウドネス正規化 ---
    t0 = time.time()
    concat_segments(job_rel, seg_files, "concat.mp4")
    bgm = select_bgm(script.get("bgm_mood") or BGM_MOOD)
    bgm_rel = f"bgm/{bgm['file']}" if bgm else None
    final = finalize_video(job_rel, "concat.mp4", bgm_rel)
    desc = write_description(job_dir, script, segments, total_sec, bgm)
    write_upload_metadata(job_dir, script, desc)
    # 代表フレームをサムネイル化（冒頭テロップが出ている頃合い）
    make_thumbnail(job_rel, min(15.0, total_sec * 0.1))
    log(f"[4/4] 結合+BGM+正規化+サムネ完了 ({time.time()-t0:.0f}s) -> {final}")

    # --- 5. YouTube アップロード（オプション） ---
    if upload:
        t0 = time.time()
        import youtube_upload
        vid = youtube_upload.upload_to_youtube(job_dir)
        if vid:
            log(f"[5/5] YouTubeアップロード完了 ({time.time()-t0:.0f}s) "
                f"video_id={vid}")

    log(f"=== ジョブ完了: {SHARED_DIR}/{job_rel}/final.mp4 ===")
    log(f"    投稿用メタ情報: {SHARED_DIR}/{job_rel}/description.txt, "
        f"upload_metadata.json, thumbnail.jpg")
    return f"{job_rel}/final.mp4"


def wait_idle() -> None:
    log("アイドル待機中。ジョブ実行: "
        "docker compose exec orchestrator python main.py --topic 'テーマ'")
    while True:
        time.sleep(3600)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--topic", default=env("JOB_TOPIC", "大規模言語モデルの仕組み"))
    p.add_argument("--mock", action=argparse.BooleanOptionalAction,
                   default=env("MOCK_PIPELINE", "false").lower() == "true",
                   help="台本生成をモック化（--no-mock で実LLMを強制）")
    p.add_argument("--upload", action=argparse.BooleanOptionalAction,
                   default=env("YT_UPLOAD", "false").lower() == "true",
                   help="完了後に YouTube へ非公開アップロード")
    p.add_argument("--run-mode", choices=["env", "once"], default="once")
    args = p.parse_args()

    if args.run_mode == "env":
        if env("RUN_ON_STARTUP", "false").lower() == "true":
            try:
                run_pipeline(args.topic, args.mock, upload=args.upload)
            except Exception as e:
                log(f"ジョブ失敗: {e}")
        wait_idle()
    else:
        run_pipeline(args.topic, args.mock, upload=args.upload)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        log(f"FATAL: {e}")
        sys.exit(1)
