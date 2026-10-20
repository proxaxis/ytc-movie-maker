#!/usr/bin/env python3
"""YouTube Data API v3 アップローダー。

- OAuth 2.0 リフレッシュトークンでアクセストークンを自動更新
- resumable upload で動画投稿（既定 privacyStatus=private）
- thumbnails.set で代表フレーム JPEG を設定
- status.containsSyntheticMedia=true（AI生成コンテンツ申告）を付与

必要スコープ: https://www.googleapis.com/auth/youtube.upload
"""
import json
import os

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_INIT_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"

HTTP = requests.Session()
HTTP_TIMEOUT = (10, 1800)  # アップロードは動画サイズ次第で長い


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def get_access_token() -> str:
    """リフレッシュトークンからアクセストークンを取得（自動更新）。"""
    resp = HTTP.post(TOKEN_URL, data={
        "client_id": env("YT_CLIENT_ID"),
        "client_secret": env("YT_CLIENT_SECRET"),
        "refresh_token": env("YT_REFRESH_TOKEN"),
        "grant_type": "refresh_token",
    }, timeout=(10, 60))
    resp.raise_for_status()
    return resp.json()["access_token"]


def _upload_body(meta: dict) -> dict:
    return {
        "snippet": {
            "title": meta["title"],
            "description": meta.get("description", ""),
            "tags": meta.get("tags", []),
            "categoryId": str(meta.get("categoryId", "27")),  # 27=Education
        },
        "status": {
            "privacyStatus": meta.get("privacyStatus", "private"),
            "selfDeclaredMadeForKids": bool(meta.get("madeForKids", False)),
            # 改変・合成コンテンツ（AI生成）の申告
            "containsSyntheticMedia": bool(
                meta.get("containsSyntheticMedia", True)),
        },
    }


def upload_video(video_path: str, meta: dict, token: str) -> str:
    """resumable upload で動画を投稿し、video_id を返す。"""
    size = os.path.getsize(video_path)
    init = HTTP.post(
        UPLOAD_INIT_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(size),
        },
        json=_upload_body(meta),
        timeout=(10, 60),
    )
    if init.status_code not in (200, 201):
        raise RuntimeError(
            f"upload init failed: {init.status_code} {init.text[:500]}")
    upload_url = init.headers["Location"]

    with open(video_path, "rb") as f:
        put = HTTP.put(upload_url, data=f,
                       headers={"Content-Type": "video/mp4"},
                       timeout=HTTP_TIMEOUT)
    put.raise_for_status()
    return put.json()["id"]


def set_thumbnail(video_id: str, thumb_path: str, token: str) -> None:
    with open(thumb_path, "rb") as f:
        resp = HTTP.post(
            THUMBNAIL_URL,
            params={"videoId": video_id},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "image/jpeg"},
            data=f,
            timeout=(10, 120),
        )
    resp.raise_for_status()


def upload_to_youtube(job_dir: str) -> str | None:
    """ジョブディレクトリの final.mp4 + upload_metadata.json (+thumbnail.jpg)
    をアップロードする。認証情報未設定なら None を返してスキップ。"""
    if not all([env("YT_CLIENT_ID"), env("YT_CLIENT_SECRET"),
                env("YT_REFRESH_TOKEN")]):
        print("[upload] YT_* 認証情報未設定 → スキップ", flush=True)
        return None

    video_path = os.path.join(job_dir, "final.mp4")
    meta_path = os.path.join(job_dir, "upload_metadata.json")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    meta.setdefault("privacyStatus", env("YT_PRIVACY", "private"))

    token = get_access_token()
    print(f"[upload] アップロード開始: {meta['title']!r} "
          f"({os.path.getsize(video_path)/1e6:.1f}MB, "
          f"privacy={meta['privacyStatus']})", flush=True)
    video_id = upload_video(video_path, meta, token)
    print(f"[upload] 完了 video_id={video_id} "
          f"https://studio.youtube.com/video/{video_id}", flush=True)

    thumb_path = os.path.join(job_dir, "thumbnail.jpg")
    if os.path.exists(thumb_path):
        set_thumbnail(video_id, thumb_path, token)
        print("[upload] サムネイル設定完了", flush=True)
    return video_id
