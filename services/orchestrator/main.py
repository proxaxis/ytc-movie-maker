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
import io
import json
import os
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
RENDERER_URL = env("RENDERER_URL", "http://remotion-renderer:3100").rstrip("/")
SHARED_DIR = env("SHARED_DIR", "/shared-data")
FPS = int(env("VIDEO_FPS", "30"))
WIDTH = int(env("VIDEO_WIDTH", "1920"))
HEIGHT = int(env("VIDEO_HEIGHT", "1080"))
TARGET_MIN = float(env("TARGET_DURATION_MIN", "10"))
CHAPTER_COUNT = int(env("CHAPTER_COUNT", "6"))
GAP_SEC = float(env("SENTENCE_GAP_SEC", "0.35"))
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
  "chapters": [
    {{"id": "ch1", "title": "チャプター見出し", "brief": "この章で説明すること(1文)"}}
  ]
}}
チャプターはちょうど{CHAPTER_COUNT}個。idは ch1, ch2, ... とすること。"""
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
bullets は3〜5個の短い箇条書き。"""
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
                for j in range(1, n_sent + 1)
            ],
            "image_prompt": "",
        })
    log(f"[MOCK] {CHAPTER_COUNT}チャプター×{n_sent}文の台本を生成")
    return {"title": f"【テスト】{topic}", "chapters": chapters}


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
    s = HTTP.post(
        f"{VOICEVOX_BASE_URL}/synthesis",
        params={"speaker": VOICEVOX_SPEAKER},
        json=q.json(),
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
    for j, text in enumerate(chapter["narration"]):
        wav = synthesize_wav(text)
        dur = wav_duration_sec(wav)
        fname = f"s{j:02d}.wav"
        with open(os.path.join(audio_dir, fname), "wb") as f:
            f.write(wav)
        sentences.append({
            "text": text,
            "audio": f"{rel_dir}/{fname}",
            "startSec": round(cursor, 3),
            "durationSec": round(dur, 3),
        })
        cursor += dur + GAP_SEC
    return sentences


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


# ---------------------------------------------------------------
# パイプライン本体
# ---------------------------------------------------------------
def run_pipeline(topic: str, mock: bool, job_id: str | None = None) -> str:
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

    manifest = {
        "jobId": job_id,
        "title": script["title"],
        "fps": FPS, "width": WIDTH, "height": HEIGHT,
        "gapSec": GAP_SEC,
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

    # --- 4. FFmpeg concat ---
    t0 = time.time()
    final = concat_segments(job_rel, seg_files, "final.mp4")
    log(f"[4/4] 結合完了 ({time.time()-t0:.0f}s) -> {final}")
    log(f"=== ジョブ完了: {SHARED_DIR}/{job_rel}/final.mp4 ===")
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
    p.add_argument("--run-mode", choices=["env", "once"], default="once")
    args = p.parse_args()

    if args.run_mode == "env":
        if env("RUN_ON_STARTUP", "false").lower() == "true":
            try:
                run_pipeline(args.topic, args.mock)
            except Exception as e:
                log(f"ジョブ失敗: {e}")
        wait_idle()
    else:
        run_pipeline(args.topic, args.mock)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        log(f"FATAL: {e}")
        sys.exit(1)
