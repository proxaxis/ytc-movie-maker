#!/usr/bin/env python3
"""YouTube アップロード用 OAuth 2.0 リフレッシュトークンの初回取得ツール。

前提: Google Cloud Console で OAuth クライアント（デスクトップアプリ or Web）
を作成し、YouTube Data API v3 を有効化しておくこと。

使い方（ホスト or コンテナ内で実行）:
    python get_refresh_token.py --client-id XXX --client-secret YYY
→ 表示されたURLをブラウザで開き、Googleアカウントで認可
→ localhost:8085 にリダイレクトされ、REFRESH_TOKEN が表示される
→ .env の YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN に設定
"""
import argparse
import http.server
import json
import sys
import threading
import urllib.parse
import urllib.request

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
PORT = 8085


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--client-id", required=True)
    p.add_argument("--client-secret", required=True)
    args = p.parse_args()
    redirect = f"http://localhost:{PORT}/"

    params = urllib.parse.urlencode({
        "client_id": args.client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",  # refresh_token を必ず発行させる
    })
    print(f"\n以下のURLをブラウザで開いて認可してください:\n\n{AUTH_URL}?{params}\n")

    code_holder: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code_holder["code"] = q.get("code", [None])[0]
            code_holder["error"] = q.get("error", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK. You can close this tab.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"localhost:{PORT} でコールバック待機中...")
    while "code" not in code_holder:
        threading.Event().wait(0.5)
    srv.shutdown()

    if code_holder.get("error") or not code_holder.get("code"):
        print(f"認可失敗: {code_holder}", file=sys.stderr)
        sys.exit(1)

    req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode({
        "code": code_holder["code"],
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    }).encode())
    tokens = json.load(urllib.request.urlopen(req))
    print("\n=== .env に設定 ===")
    print(f"YT_CLIENT_ID={args.client_id}")
    print(f"YT_CLIENT_SECRET={args.client_secret}")
    print(f"YT_REFRESH_TOKEN={tokens['refresh_token']}")


if __name__ == "__main__":
    main()
