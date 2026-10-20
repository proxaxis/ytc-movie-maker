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
IMAGEGEN_URL = env("IMAGEGEN_URL", "http://imagegen:8100").rstrip("/")
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
    env("CHARACTER_IMAGE", "characters/tsumugi-1.png"))
CHARACTER_CREDIT = env(
    "CHARACTER_CREDIT", "VOICEVOX:春日部つむぎ / 立ち絵=春日部つくし")
# 固定テーマカラー（空ならジョブごとのランダム色相にフォールバック）
ACCENT_COLOR = env("ACCENT_COLOR", "")
ACCENT_COLOR2 = env("ACCENT_COLOR2", "")
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


def extract_json(text: str):
    """LLM出力から最初のJSON(オブジェクトまたは配列)を頑健に抽出する。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"[\{\[].*[\}\]]", text, re.DOTALL)
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
    chapters = None
    data = {}
    for attempt in (1, 2):
        data = extract_json(ollama_generate(prompt, num_predict=1024))
        # キー名の揺れを吸収
        raw = (data.get("chapters") or data.get("chapter_list")
               or data.get("outline") or data.get("toc") or [])
        if isinstance(raw, dict):
            raw = list(raw.values())
        if isinstance(raw, list) and raw:
            chapters = raw
            break
        log(f"[LLM:1/2] chapters未取得 (attempt {attempt}) keys={list(data)}")
    if not chapters:
        # 小型モデルがネスト構造を落とす場合のフォールバック:
        # チャプタータイトルの配列だけを要求する最小プロンプト
        log("[LLM:1/2] 簡易モード（タイトル配列のみ）で再生成")
        simple = f"""テーマ「{topic}」の解説動画のチャプタータイトルを
ちょうど{CHAPTER_COUNT}個、JSONの配列のみで出力せよ。
出力例: ["導入", "基礎知識", "歴史", "仕組み", "応用", "まとめ"]"""
        for _ in (1, 2):
            arr = extract_json(ollama_generate(simple, num_predict=512))
            if isinstance(arr, list) and arr:
                chapters = arr
                break
            if isinstance(arr, dict):
                vals = [v for v in arr.values() if isinstance(v, list)]
                if vals and vals[0]:
                    chapters = vals[0]
                    break
    if not chapters:
        raise ValueError(f"LLMがチャプター構成を返しませんでした: {str(data)[:200]}")

    # 文字列のみの要素も {title,brief} に正規化
    norm = []
    for i, ch in enumerate(chapters[:CHAPTER_COUNT], 1):
        if isinstance(ch, str):
            ch = {"title": ch}
        norm.append({
            "id": f"ch{i}",
            "title": str(ch.get("title", f"チャプター{i}")),
            "brief": str(ch.get("brief", "")),
        })
    data["chapters"] = norm
    data.setdefault("title", topic)
    log(f"[LLM:1/2] {len(norm)}チャプター: " +
        ", ".join(c["title"] for c in norm))
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

【文体ルール】人間が書いたような自然で温度感のある日本語にすること:
- 「結論から言うと」「以下で解説します」「〜と言えるでしょう」等の構造宣言・決まり文句は禁止
- 「一般的に」「場合によります」等の保険をかける表現は禁止
- 接続詞(「また」「さらに」「しかしながら」)は極力使わず自然な流れにする
- 「〜です/〜ます」が3回以上連続しないよう体言止めや言い切りを混ぜる
- 抽象的なまとめで終わらず、読み物のような自然な余韻で締める
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
    data["narration"] = sanitize_narration(data["narration"])
    if len(data["narration"]) < 3:
        raise ValueError(f"有効なセリフ不足: {len(data['narration'])}文")
    log(f"[LLM:2/2] {chapter['id']}: {len(data['narration'])}文, "
        f"{len(data['bullets'])}要点")
    return data


def verify_chapter(chapter: dict) -> dict:
    """stage2.5: ナレーションの事実誤認（数値・年号・固有名詞）をLLMで検証・訂正。
    失敗時は元の内容をそのまま使う。"""
    facts_env = env("FACT_CHECK", "true").lower() == "true"
    if not facts_env:
        return chapter
    text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(chapter["narration"]))
    prompt = f"""以下は解説動画チャプター「{chapter['title']}」のナレーション文です。
事実誤認（数値・年号・企業名・規格値・固有名詞）だけを修正してください。
表現の好み・言い回しは変えないこと。修正不要ならそのまま返すこと。

{text}

出力は必ず以下のJSON形式のみ:
{{"narration": ["修正後の文1。", "修正後の文2。"]}}
文数は元と同じ{len(chapter['narration'])}文に保つこと。"""
    try:
        log(f"[LLM:検証] {chapter['id']} の事実チェック中...")
        data = extract_json(ollama_generate(prompt, num_predict=3000))
        fixed = data.get("narration")
        if isinstance(fixed, list) and len(fixed) >= len(chapter["narration"]) * 0.7:
            before = sanitize_narration(fixed)
            if before:
                chapter["narration"] = before
                log(f"[LLM:検証] {chapter['id']}: {len(before)}文に更新")
    except Exception as e:
        log(f"[LLM:検証] {chapter['id']} 失敗→原文維持: {e}")
    return chapter


_PLACEHOLDER_RE = re.compile(
    r"^[\s　]*(ナレーション文?\d*[。.:：、]?|ナレーション[:：])\s*")


def sanitize_narration(narration: list) -> list:
    """プレースホルダ文頭除去・全角数字正規化・重複文除去。"""
    seen = set()
    out = []
    for s in narration:
        s = _PLACEHOLDER_RE.sub("", str(s)).strip()
        if not s or len(s) < 8:
            continue
        # 空白・句点を潰したキーで近似重複判定
        key = re.sub(r"[。\s　、]", "", s)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


_NUM = "零一二三四五六七八九十"


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
                f"これは{topic}に関するチャプター{i}のテストナレーション、"
                f"{_NUM[j]}番目です。"
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
def synthesize_wav(text: str) -> tuple[bytes, str]:
    """audio_query → synthesis でWAVと推定読み(kana)を取得。"""
    q = HTTP.post(
        f"{VOICEVOX_BASE_URL}/audio_query",
        params={"text": text, "speaker": VOICEVOX_SPEAKER},
        timeout=HTTP_TIMEOUT,
    )
    q.raise_for_status()
    query = q.json()
    kana = query.get("kana", "")  # VOICEVOXが推定した実際の読み（レビュー用）
    query["speedScale"] = VOICEVOX_SPEED  # ナレーション速度
    s = HTTP.post(
        f"{VOICEVOX_BASE_URL}/synthesis",
        params={"speaker": VOICEVOX_SPEAKER},
        json=query,
        timeout=HTTP_TIMEOUT,
    )
    s.raise_for_status()
    return s.content, kana


def wav_duration_sec(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.getnframes() / w.getframerate()


def tts_chapter(chapter: dict, audio_dir: str, rel_dir: str) -> list:
    """1チャプター分の全セリフを音声化し、タイミング付き配列を返す。"""
    sentences = []
    cursor = 0.0
    # 見切れ防止: 長すぎるセリフは句読点で分割（字幕は1〜2行想定）
    raw_list = []
    for raw in chapter["narration"]:
        if len(raw) > 90:
            parts = re.split(r"(?<=[。、！？])", raw)
            buf = ""
            for p in parts:
                if len(buf) + len(p) > 80 and buf:
                    raw_list.append(buf)
                    buf = p
                else:
                    buf += p
            if buf:
                raw_list.append(buf)
        else:
            raw_list.append(raw)
    for j, raw in enumerate(raw_list):
        display = annotated_display(raw)          # 字幕: 漢字表記のまま
        speech = apply_fixes(annotated_speech(raw))  # 音声: かな読み+矯正
        wav, kana = synthesize_wav(speech)
        dur = wav_duration_sec(wav)
        fname = f"s{j:02d}.wav"
        with open(os.path.join(audio_dir, fname), "wb") as f:
            f.write(wav)
        sentences.append({
            "text": display,
            "kana": kana,  # 実際に読まれた読み（誤読レビュー用）
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
    # 実在するファイルのみ選択対象にする
    tracks = [t for t in tracks
              if os.path.exists(os.path.join(SHARED_DIR, "bgm", t["file"]))]
    if not tracks:
        log("[BGM] 有効なファイルなし → BGMなしで続行")
        return None
    mood_l = (mood or "").lower()
    matched = [t for t in tracks
               if mood_l and mood_l in [m.lower() for m in t.get("mood", [])]]
    track = random.choice(matched or tracks)
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


def gen_chapter_image(chapter: dict, job_rel: str) -> str | None:
    """章の image_prompt をローカルSDで生成。失敗時 None。"""
    prompt = chapter.get("image_prompt")
    if not prompt:
        return None
    rel = f"{job_rel}/images/{chapter['id']}.png"
    try:
        r = HTTP.post(f"{IMAGEGEN_URL}/generate",
                      json={"prompt": prompt, "out_path": rel},
                      timeout=(10, 300))
        r.raise_for_status()
        return rel
    except Exception as e:
        log(f"[画像] {chapter['id']} 生成スキップ: {e}")
        return None


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
                    detailed.append(
                        verify_chapter(stage2_chapter(topic, script["title"], ch)))
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
        img_rel = gen_chapter_image(ch, job_rel)
        ch["_image"] = img_rel
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
            "image": ch.get("_image"),
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
        "accentColor": ACCENT_COLOR or None,
        "accentColor2": ACCENT_COLOR2 or None,
        "bgSeed": int(hashlib.md5(job_id.encode()).hexdigest()[:8], 16),
        "bgStyle": env("BG_STYLE", "game"),
        "characterImage": char_paths[0] if has_char else None,
        "characterWink": char_paths[1] if len(char_paths) > 1 else None,
        "totalDurationSec": round(total_sec, 2),
        "segments": segments,
    }
    # レイアウト見切れチェックフェーズ
    warns = []
    for seg in segments:
        if len(seg["title"]) > 28:
            warns.append(f"{seg['id']} タイトル長 {len(seg['title'])}字")
        if len(seg["bullets"]) > 5:
            warns.append(f"{seg['id']} 箇条書き {len(seg['bullets'])}個")
        for s in seg["sentences"]:
            if len(s["text"]) > 80:
                warns.append(f"{seg['id']} 字幕長 {len(s['text'])}字: {s['text'][:30]}…")
        for b in seg["bullets"]:
            if len(str(b)) > 40:
                warns.append(f"{seg['id']} 要点長 {len(str(b))}字")
    if warns:
        log(f"[見切れチェック] 警告{len(warns)}件: " + " | ".join(warns[:8]))
    else:
        log("[見切れチェック] OK")

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
