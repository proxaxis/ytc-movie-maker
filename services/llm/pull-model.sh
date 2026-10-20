#!/bin/sh
# Ollama モデルを pull するセットアップスクリプト。
# docker compose の llm-init サービスがこれと同等の処理を自動実行するが、
# ホストから手動で追加モデルを入れたい場合にも使える。
#
#   ./services/llm/pull-model.sh                # .env の OLLAMA_MODEL
#   ./services/llm/pull-model.sh mistral:7b     # 明示指定
set -eu

MODEL="${1:-${OLLAMA_MODEL:-llama3.2:3b}}"
HOST="${OLLAMA_HOST:-http://localhost:11434}"

echo "Pulling ${MODEL} from ${HOST} ..."
curl -sf "${HOST}/api/pull" -d "{\"name\":\"${MODEL}\",\"stream\":false}" \
  | grep -o '"status":"[^"]*"' | tail -1
echo "Done. Installed models:"
curl -sf "${HOST}/api/tags"
echo
